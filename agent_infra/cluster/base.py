"""Abstract cluster provider interface."""

from abc import ABC, abstractmethod
from typing import Any, Optional


class ClusterProvider(ABC):
    """Abstract interface for cluster job management.

    Implementations can support different cluster types:
    - SLURM (HPC clusters)
    - Local (development/testing)
    - Kubernetes (future)
    - Cloud providers (future)
    """

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this cluster type is available.

        Returns:
            True if the cluster management commands are accessible
        """
        pass

    @abstractmethod
    def allocate(
        self,
        model_name: str,
        gpus_per_replica: int,
        num_replicas: int,
    ) -> list[dict[str, Any]]:
        """Allocate resources for model replicas.

        Args:
            model_name: Name of the model to deploy
            gpus_per_replica: Number of GPUs needed per replica
            num_replicas: Number of replicas to allocate

        Returns:
            List of allocation dicts, each with:
            - partition: str - partition/queue name
            - gpus: int - GPUs per replica
            - count: int - number of replicas on this partition
            - qos: str - quality of service (optional)
        """
        pass

    @abstractmethod
    def submit_job(
        self,
        script_path: str,
        allocation: dict[str, Any],
    ) -> str:
        """Submit a job to the cluster.

        Args:
            script_path: Path to job script
            allocation: Allocation dict from allocate()

        Returns:
            Job ID string
        """
        pass

    @abstractmethod
    def list_jobs(self, user: Optional[str] = None) -> Optional[dict[str, dict]]:
        """List running jobs.

        Args:
            user: Filter by user (None = current user)

        Returns:
            Dict mapping job_id to job info, or None if unavailable
            Each job info contains: node, command, partition
        """
        pass

    @abstractmethod
    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job.

        Args:
            job_id: Job ID to cancel

        Returns:
            True if cancelled successfully
        """
        pass

    def get_job_info(self, job_id: str) -> Optional[dict[str, Any]]:
        """Get detailed info about a specific job.

        Args:
            job_id: Job ID to query

        Returns:
            Job info dict or None if not found
        """
        jobs = self.list_jobs()
        if jobs and job_id in jobs:
            return jobs[job_id]
        return None
