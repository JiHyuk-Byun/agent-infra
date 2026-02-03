"""Abstract server launcher interface."""

from abc import ABC, abstractmethod
from typing import Optional


class ServerLauncher(ABC):
    """Abstract interface for launching LLM inference servers."""

    @abstractmethod
    def is_running(self, host: str, port: int, model: str) -> bool:
        """Check if server is already running with the specified model.

        Args:
            host: Server host
            port: Server port
            model: Expected model name

        Returns:
            True if server is running and serving the model
        """
        pass

    @abstractmethod
    def launch(
        self,
        model: str,
        host: str,
        port: int,
        gpu_memory_utilization: float = 0.85,
        max_model_len: Optional[int] = None,
        trust_remote_code: bool = False,
    ) -> None:
        """Launch the inference server.

        Args:
            model: Model name or path
            host: Host to bind
            port: Port to bind
            gpu_memory_utilization: GPU memory fraction to use
            max_model_len: Maximum model context length
            trust_remote_code: Whether to trust remote code
        """
        pass

    @abstractmethod
    def stop(self, port: int) -> bool:
        """Stop a running server.

        Args:
            port: Port the server is running on

        Returns:
            True if server was stopped
        """
        pass
