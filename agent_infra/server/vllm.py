"""vLLM server launcher."""

from __future__ import annotations

import json
import math
import os
import signal
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from agent_infra.server.base import ServerLauncher


def _deduce_max_tensor_parallel() -> int:
    """Determine optimal tensor parallel size based on available GPUs."""
    try:
        import torch
        num_gpus = torch.cuda.device_count()
        return int(2 ** (math.floor(math.log(num_gpus, 2))))
    except Exception:
        return 1


def fetch_running_models(host: str, port: int, timeout: float = 2.0) -> Optional[list[str]]:
    """Fetch list of models from a running vLLM server."""
    url = f"http://{host}:{port}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            models = data.get("data") or []
            model_ids = [row.get("id") for row in models if isinstance(row, dict)]
            return [m for m in model_ids if m]
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def find_pids_on_port(port: int) -> list[int]:
    """Best-effort PID lookup using lsof."""
    try:
        res = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            return []
        pids = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        return [int(pid) for pid in pids]
    except FileNotFoundError:
        return []


def kill_pids(pids: list[int]) -> None:
    """Kill processes by PID."""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue


def find_available_port(start_port: int, retries: int = 200) -> int:
    """Find an available port starting from start_port."""
    for port in range(start_port, start_port + retries):
        try:
            s = socket.socket()
            s.bind(("", port))
            s.close()
            return port
        except OSError:
            continue
    raise RuntimeError(f"No free port found in range {start_port}-{start_port+retries}")


def launch_vllm(
    model: str,
    host: str = "0.0.0.0",
    port: int = 5000,
    tensor_parallel_size: Optional[int] = None,
    gpu_memory_utilization: float = 0.85,
    max_model_len: Optional[int] = None,
    trust_remote_code: bool = False,
    restart_if_mismatch: bool = False,
) -> None:
    """Launch a vLLM OpenAI-compatible server.

    Args:
        model: Model name or HuggingFace path
        host: Host to bind (default: 0.0.0.0)
        port: Port to bind (default: 5000)
        tensor_parallel_size: TP size (default: auto-detect)
        gpu_memory_utilization: GPU memory fraction (default: 0.85)
        max_model_len: Maximum model length (optional)
        trust_remote_code: Trust remote code for custom models
        restart_if_mismatch: Kill existing server if model doesn't match
    """
    running = fetch_running_models(host, port)
    if running is not None:
        if model in running:
            print(f"vLLM already running on {host}:{port} serving model '{model}'. Reusing.")
            return
        if restart_if_mismatch:
            pids = find_pids_on_port(port)
            if pids:
                print(f"Found existing server on port {port} without model '{model}'. Killing PIDs: {pids}")
                kill_pids(pids)
            else:
                print(f"No killable PIDs found on port {port}, proceeding to start a new server.")
        else:
            print(f"A server is running on {host}:{port} but does not serve '{model}'. "
                  "Use restart_if_mismatch=True to replace it.")
            sys.exit(1)

    used_port = find_available_port(port)
    tp = tensor_parallel_size or _deduce_max_tensor_parallel()

    cmd = [
        sys.executable,
        "-m", "vllm.entrypoints.openai.api_server",
        "--model", model,
        "--host", host,
        "--port", str(used_port),
        "--tensor-parallel-size", str(tp),
        "--gpu-memory-utilization", str(gpu_memory_utilization),
    ]

    if trust_remote_code:
        cmd.append("--trust-remote-code")
    if max_model_len:
        cmd.extend(["--max-model-len", str(max_model_len)])

    local_hostname = socket.gethostname()
    print(f"Running on: http://{local_hostname}:{used_port}")
    print("Starting vLLM server:\n", " ".join(cmd))

    # Save port info for discovery
    out_name = model.replace(".", "_").replace("/", "__")
    out_path = Path(__file__).parent / "temp" / f"{out_name}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"port": used_port}))

    subprocess.run(cmd, check=True)


class VLLMLauncher(ServerLauncher):
    """vLLM server launcher implementation."""

    def is_running(self, host: str, port: int, model: str) -> bool:
        running = fetch_running_models(host, port)
        return running is not None and model in running

    def launch(
        self,
        model: str,
        host: str,
        port: int,
        gpu_memory_utilization: float = 0.85,
        max_model_len: Optional[int] = None,
        trust_remote_code: bool = False,
    ) -> None:
        launch_vllm(
            model=model,
            host=host,
            port=port,
            gpu_memory_utilization=gpu_memory_utilization,
            max_model_len=max_model_len,
            trust_remote_code=trust_remote_code,
            restart_if_mismatch=True,
        )

    def stop(self, port: int) -> bool:
        pids = find_pids_on_port(port)
        if pids:
            kill_pids(pids)
            return True
        return False
