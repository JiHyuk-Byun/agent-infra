"""Connection manager for orchestrating GPU infrastructure."""

from __future__ import annotations

import json
import os
import signal
import threading
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError

from agent_infra.config import Config
from agent_infra.cluster import SlurmProvider, get_slurm_jobs
from agent_infra.tunnel import SSHTunnelManager
from agent_infra.proxy import proxy_server


def url_accessible(url: str, timeout: float = 3.0) -> bool:
    """Check if a URL is accessible."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except HTTPError as e:
        return e.getcode() == 404
    except Exception:
        return False


def test_availability(node: str, port: int) -> bool:
    """Test if a vLLM backend is accessible."""
    return url_accessible(f"http://{node}:{port}/health")


class ConnectionManager:
    """Orchestrator for managing GPU infrastructure.

    Handles:
    - Starting GPU jobs on SLURM clusters
    - Creating SSH tunnels to remote backends
    - Running load-balancing proxy
    - Polling for new/removed backends
    """

    def __init__(self, config: Config):
        """Initialize connection manager.

        Args:
            config: Agent-infra configuration
        """
        self.config = config
        self._pid_file = Path(__file__).parent / "temp" / "proxy.pid"

        # Build model configs from config
        self._model_configs = {
            m.name: {
                "model": m.model_path,
                "port": m.base_port,
                "gpu_memory_utilization": m.gpu_memory_utilization,
                "max_model_len": m.max_model_len,
                "trust_remote_code": m.trust_remote_code,
            }
            for m in config.models
        }

        # Initialize cluster provider
        self._cluster = SlurmProvider(config.get_partition_preferences())

    def _build_endpoint(self, job: dict[str, str]) -> Optional[tuple[str, str, int, str]]:
        """Build endpoint from slurm job info.

        Returns (model, node, port, partition) or None.
        """
        node = job["node"]
        partition = job.get("partition") or ""
        command = job.get("command", "")

        if not command:
            return None

        # Extract model name from job command
        job_name = Path(command).stem
        if job_name.startswith("start_vllm_"):
            job_name = job_name[len("start_vllm_"):]

        # Strip replica suffix (e.g., "my_model_0" -> "my_model")
        parts = job_name.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            model = parts[0]
            replica_idx = int(parts[1])
        else:
            model = job_name
            replica_idx = 0

        if model not in self._model_configs:
            print(f"Warning: Unknown model '{model}' from job")
            return None

        # Calculate port (base port + replica offset)
        port = self._model_configs[model]["port"] + replica_idx * 10

        if test_availability(node, port):
            return model, node, port, partition

        return None

    def _build_endpoints(self, jobs: dict) -> list[tuple[str, str, int, str]]:
        """Build endpoint list from SLURM jobs."""
        endpoints = [self._build_endpoint(row) for row in jobs.values()]
        return [ep for ep in endpoints if ep is not None]

    def _build_backends(self, endpoints: list[tuple]) -> dict[str, list[tuple]]:
        """Build backends dict for proxy: {model: [(host, port, partition), ...]}."""
        backends = {}
        for model, node, port, partition in endpoints:
            if model not in backends:
                backends[model] = []
            backends[model].append(("localhost", port, partition))
        return backends

    def _get_allocations(self, num_replicas: int) -> list[dict]:
        """Get partition allocations for replicas."""
        from agent_infra.cluster.slurm import allocate_partitions

        preferences = self.config.get_partition_preferences()
        if not preferences:
            return [{"partition": "default", "qos": "default", "gpus": 4, "count": num_replicas}]

        return allocate_partitions(preferences, num_replicas, verbose=True)

    def _create_run_dir(self, models_to_start: list[str], replicas: int) -> Path:
        """Create a timestamped run directory."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        models_tag = "_".join(models_to_start)
        run_name = f"{timestamp}_{models_tag}_x{replicas}"

        run_dir = Path(__file__).parent.parent / "runs" / run_name
        (run_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)

        return run_dir

    def start_jobs(
        self,
        models: Optional[list[str]] = None,
        replicas: int = 1,
    ):
        """Start vLLM jobs for each model with specified replicas.

        Args:
            models: List of model names to start (None = all configured)
            replicas: Number of replicas per model
        """
        from agent_infra.cluster.slurm import build_sbatch_cmd

        models_to_start = models or list(self._model_configs.keys())

        total_jobs = len(models_to_start) * replicas
        print(f"Starting {len(models_to_start)} model(s) with {replicas} replica(s) each ({total_jobs} total jobs)\n")

        # Get partition allocations
        allocations = self._get_allocations(replicas)
        print()

        # Create run directory
        run_dir = self._create_run_dir(models_to_start, replicas)
        print(f"Run directory: {run_dir}\n")

        # Save run config
        run_config = {
            "timestamp": datetime.now().isoformat(),
            "models": models_to_start,
            "replicas": replicas,
            "allocations": allocations,
        }
        with open(run_dir / "run_config.json", "w") as f:
            json.dump(run_config, f, indent=2)

        for model in models_to_start:
            if model not in self._model_configs:
                print(f"  Warning: Unknown model '{model}', skipping")
                continue

            config = self._model_configs[model]
            replica_idx = 0

            print(f"  {model}:")
            for alloc in allocations:
                sbatch_cmd = build_sbatch_cmd(alloc)
                for _ in range(alloc["count"]):
                    self._start_job(model, config, replica_idx, replicas, sbatch_cmd, run_dir)
                    print(f"    - replica {replica_idx + 1}/{replicas} on {alloc['partition']} "
                          f"(port {config['port'] + replica_idx * 10})")
                    replica_idx += 1

    def _start_job(
        self,
        model: str,
        model_config: dict,
        replica_idx: int,
        total_replicas: int,
        sbatch_cmd: list[str],
        run_dir: Path,
    ):
        """Start a single vLLM job."""
        path = model_config["model"]
        port = model_config["port"] + replica_idx * 10

        infra_dir = Path(__file__).parent.parent.resolve()

        suffix = f"_{replica_idx}" if total_replicas > 1 else ""
        job_name = f"vllm_{model}{suffix}"

        script_dir = run_dir / "scripts"
        log_dir = run_dir / "logs"

        log_out = log_dir / f"{job_name}_%j.out"
        log_err = log_dir / f"{job_name}_%j.err"

        script = f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --output={log_out}
#SBATCH --error={log_err}

cd {infra_dir}
python -m agent_infra.server.vllm --model "{path}" --port {port}
"""

        out_path = (script_dir / f"start_vllm_{model}{suffix}.sh").resolve()
        with open(str(out_path), "w") as f:
            f.write(script)

        # Launch job
        import subprocess
        cmd = [*sbatch_cmd, str(out_path)]
        subprocess.run(cmd, check=True)

    def _poll_loop(self, tunnels, proxy, known_endpoints, stop_event, poll_interval: int):
        """Periodically poll SLURM for new/removed GPU servers."""
        while not stop_event.wait(poll_interval):
            try:
                jobs = get_slurm_jobs()
                if not jobs:
                    continue

                current_endpoints = set(self._build_endpoints(jobs))

                # New endpoints
                added = current_endpoints - known_endpoints
                for model, host, port, partition in added:
                    print(f"[POLL] New GPU discovered: {model} @ {host}:{port} ({partition})")
                    tunnels.add_tunnel(model, host, port)
                    proxy.add_backend(model, "localhost", port, partition=partition)

                # Removed endpoints
                removed = known_endpoints - current_endpoints
                for item in removed:
                    model, host, port = item[:3]
                    print(f"[POLL] GPU removed: {model} @ {host}:{port}")
                    proxy.remove_backend("localhost", port)
                    tunnels.remove_tunnel(host, port)

                known_endpoints.clear()
                known_endpoints.update(current_endpoints)

            except Exception as e:
                print(f"[POLL] Error: {e}")

    def connect(
        self,
        background: bool = False,
        poll_interval: int = 30,
    ):
        """Connect to running GPU backends with proxy.

        Args:
            background: Run in background (daemon mode)
            poll_interval: Seconds between SLURM polling (0 to disable)
        """
        jobs = get_slurm_jobs()
        if not jobs:
            print("No SLURM jobs found. Start jobs first with start_jobs().")
            return

        endpoints = self._build_endpoints(jobs)
        if not endpoints:
            print("No accessible GPU backends found.")
            return

        backends = self._build_backends(endpoints)
        proxy_port = self.config.proxy.port
        strategy = self.config.proxy.strategy

        with SSHTunnelManager(endpoints) as tunnels:
            with proxy_server(
                backends,
                proxy_port,
                strategy,
                headers_config=self.config.headers,
            ) as proxy:
                print(f"\nProxy running at http://localhost:{proxy_port}")
                print(f"  - /v1/chat/completions  (auto-detect model)")
                print(f"  - /{{model}}/v1/...     (explicit model)")
                print(f"  - /stats                (view statistics)")

                # Start polling thread
                stop_event = threading.Event()
                known = set(endpoints)

                if poll_interval > 0:
                    poll_thread = threading.Thread(
                        target=self._poll_loop,
                        args=(tunnels, proxy, known, stop_event, poll_interval),
                        daemon=True,
                    )
                    poll_thread.start()
                    print(f"  - SLURM polling every {poll_interval}s")

                try:
                    self._wait_for_signal(background)
                finally:
                    stop_event.set()
                    if poll_interval > 0:
                        poll_thread.join(timeout=5)

    def _wait_for_signal(self, background: bool):
        """Wait for user input or signal depending on mode."""
        if background:
            self._pid_file.parent.mkdir(parents=True, exist_ok=True)
            self._pid_file.write_text(str(os.getpid()))
            print(f"Running in background (PID: {os.getpid()})")
            print("Stop with: agent-infra stop")
            event = signal.sigwait([signal.SIGINT, signal.SIGTERM])
            print(f"\nReceived signal {event}, shutting down...")
            self._pid_file.unlink(missing_ok=True)
        else:
            input("\nPress Enter to close.\n")

    def stop(self) -> bool:
        """Stop a background proxy process."""
        if not self._pid_file.exists():
            print("No background proxy running (PID file not found)")
            return False

        try:
            pid = int(self._pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            print(f"Sent SIGTERM to process {pid}")
            self._pid_file.unlink(missing_ok=True)
            return True
        except ProcessLookupError:
            print("Process not found, cleaning up PID file")
            self._pid_file.unlink(missing_ok=True)
            return False
        except Exception as e:
            print(f"Error stopping process: {e}")
            return False
