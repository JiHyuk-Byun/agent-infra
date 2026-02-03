"""Backend and backend pool management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Backend:
    """A single backend server."""
    host: str
    port: int
    healthy: bool = True
    last_check: float = 0
    request_count: int = 0
    error_count: int = 0
    avg_latency_ms: float = 0
    # GPU load metrics for least_load strategy
    gpu_load: int = 0
    load_last_updated: float = 0
    # Local in-flight counter: requests dispatched but not yet completed
    inflight: int = 0
    # Consecutive timeout counter for unhealthy detection
    consecutive_timeouts: int = 0
    # Partition this backend runs on (SLURM)
    partition: str = ""

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def record_request(self, latency_ms: float, success: bool):
        """Record a completed request for statistics."""
        self.request_count += 1
        if success:
            # Exponential moving average for latency
            alpha = 0.2
            self.avg_latency_ms = alpha * latency_ms + (1 - alpha) * self.avg_latency_ms
        else:
            self.error_count += 1


@dataclass
class BackendPool:
    """Pool of backends for a single model with load balancing."""
    model: str
    backends: list[Backend] = field(default_factory=list)
    _index: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def add_backend(self, host: str, port: int, partition: str = ""):
        """Add a backend to the pool."""
        # Check if backend already exists
        for b in self.backends:
            if b.host == host and b.port == port:
                b.healthy = True  # Mark as healthy on re-registration
                if partition:
                    b.partition = partition
                return
        self.backends.append(Backend(host=host, port=port, partition=partition))

    def remove_backend(self, host: str, port: int) -> bool:
        """Remove a backend from the pool. Returns True if removed."""
        for i, b in enumerate(self.backends):
            if b.host == host and b.port == port:
                self.backends.pop(i)
                return True
        return False

    async def get_backend(self, strategy: str = "least_load") -> Optional[Backend]:
        """Get next available backend using specified strategy.

        The selected backend's inflight counter is always incremented
        atomically while the lock is held. The caller MUST decrement
        it when the request completes (use try/finally).
        """
        from agent_infra.proxy.strategies import select_backend

        async with self._lock:
            healthy_backends = [b for b in self.backends if b.healthy]
            if not healthy_backends:
                return None

            backend, self._index = select_backend(healthy_backends, strategy, self._index)
            backend.inflight += 1
            return backend

    def get_stats(self) -> dict[str, Any]:
        """Get pool statistics."""
        return {
            "model": self.model,
            "backends": [
                {
                    "url": b.url,
                    "healthy": b.healthy,
                    "requests": b.request_count,
                    "errors": b.error_count,
                    "avg_latency_ms": round(b.avg_latency_ms, 2),
                    "inflight": b.inflight,
                    "partition": b.partition,
                }
                for b in self.backends
            ],
        }
