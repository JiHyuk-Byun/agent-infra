"""
Agent Infra - GPU serving infrastructure for multi-turn LLM agents.

This package provides:
- Load-balancing proxy for vLLM backends
- SLURM cluster integration for GPU job management
- SSH tunnel automation
- Session tracking for multi-turn agents
"""

from agent_infra.config import load_config, Config
from agent_infra.client import SessionContext
from agent_infra.orchestrator import ConnectionManager

__version__ = "0.1.0"

__all__ = [
    "load_config",
    "Config",
    "SessionContext",
    "ConnectionManager",
    "__version__",
]
