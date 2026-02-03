"""Local cluster provider for development and testing."""

from __future__ import annotations

import subprocess
from typing import Any, Optional

from agent_infra.cluster.base import ClusterProvider


class LocalProvider(ClusterProvider):
    """Local development cluster provider.

    Runs processes directly on the local machine without job scheduling.
    Useful for testing and development without access to SLURM.
    """

    def __init__(self):
        self._processes: dict[str, subprocess.Popen] = {}
        self._next_id = 1

    def is_available(self) -> bool:
        """Always available for local development."""
        return True

    def allocate(
        self,
        model_name: str,
        gpus_per_replica: int,
        num_replicas: int,
    ) -> list[dict[str, Any]]:
        """Return simple local allocation."""
        return [{
            "partition": "local",
            "gpus": gpus_per_replica,
            "count": num_replicas,
            "qos": "default",
        }]

    def submit_job(
        self,
        script_path: str,
        allocation: dict[str, Any],
    ) -> str:
        """Run script as local subprocess."""
        job_id = f"local_{self._next_id}"
        self._next_id += 1

        proc = subprocess.Popen(
            ["bash", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._processes[job_id] = proc
        return job_id

    def list_jobs(self, user: Optional[str] = None) -> Optional[dict[str, dict]]:
        """List running local processes."""
        results = {}
        for job_id, proc in list(self._processes.items()):
            if proc.poll() is None:  # Still running
                results[job_id] = {
                    "node": "localhost",
                    "command": f"PID: {proc.pid}",
                    "partition": "local",
                }
            else:
                # Process finished, clean up
                del self._processes[job_id]
        return results if results else None

    def cancel_job(self, job_id: str) -> bool:
        """Terminate local process."""
        if job_id in self._processes:
            proc = self._processes[job_id]
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            del self._processes[job_id]
            return True
        return False
