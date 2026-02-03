"""Load balancing strategies for backend selection."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_infra.proxy.backend import Backend

# Available strategies
STRATEGIES = ["round_robin", "least_connections", "least_latency", "least_load"]


def select_backend(
    backends: list["Backend"],
    strategy: str,
    current_index: int,
) -> tuple["Backend", int]:
    """Select a backend using the specified strategy.

    Args:
        backends: List of healthy backends to choose from
        strategy: Load balancing strategy name
        current_index: Current round-robin index

    Returns:
        Tuple of (selected backend, new index)
    """
    if not backends:
        raise ValueError("No backends available")

    if strategy == "round_robin":
        backend = backends[current_index % len(backends)]
        return backend, current_index + 1

    elif strategy == "least_connections":
        # Route to backend with fewest active requests
        min_inflight = min(b.inflight for b in backends)
        tied = [b for b in backends if b.inflight == min_inflight]
        backend = tied[current_index % len(tied)]
        return backend, current_index + 1

    elif strategy == "least_latency":
        # Route to fastest responding backend
        min_lat = min(b.avg_latency_ms for b in backends)
        tied = [b for b in backends if b.avg_latency_ms == min_lat]
        backend = tied[current_index % len(tied)]
        return backend, current_index + 1

    elif strategy == "least_load":
        # Composite score: gpu_load (remote metric) + inflight (local counter)
        min_score = min(b.gpu_load + b.inflight for b in backends)
        tied = [b for b in backends if b.gpu_load + b.inflight == min_score]
        backend = tied[current_index % len(tied)]
        return backend, current_index + 1

    else:
        # Unknown strategy, fall back to first backend
        return backends[0], current_index
