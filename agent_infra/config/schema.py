"""Pydantic configuration schemas for agent-infra."""

from typing import Literal, Optional
from pydantic import BaseModel, Field


class HeadersConfig(BaseModel):
    """Configurable header names for request tracking.

    These headers are used by the proxy to track requests and by clients
    to identify sessions. Different projects may use different header names.
    """
    session: str = Field(default="X-Session-ID", description="Session identifier header")
    task: str = Field(default="X-Task-ID", description="Task/instruction identifier header")
    client: str = Field(default="X-Client-ID", description="Client process identifier header")
    timing_pre: str = Field(default="X-Timing-Pre-Ms", description="Pre-request timing (e.g., observation build)")
    timing_post: str = Field(default="X-Timing-Post-Ms", description="Post-request timing (e.g., action execution)")


class ProxyConfig(BaseModel):
    """Load-balancing proxy configuration."""
    port: int = Field(default=5800, description="Port for the proxy server")
    strategy: Literal["round_robin", "least_connections", "least_latency", "least_load"] = Field(
        default="least_load",
        description="Load balancing strategy"
    )
    health_check_interval: int = Field(default=30, description="Health check interval in seconds")
    request_timeout: int = Field(default=300, description="Request timeout in seconds")
    verbose: bool = Field(default=True, description="Enable verbose logging")


class PartitionConfig(BaseModel):
    """SLURM partition configuration."""
    name: str = Field(description="Partition name")
    qos: str = Field(default="default", description="Quality of service")
    gpus_per_node: int = Field(default=4, description="GPUs available per node")
    priority: int = Field(default=1, description="Selection priority (lower = preferred)")


class SlurmConfig(BaseModel):
    """SLURM cluster-specific configuration."""
    partitions: list[PartitionConfig] = Field(default_factory=list, description="Partition preferences")
    default_qos: str = Field(default="default", description="Default QOS if not specified")


class ClusterConfig(BaseModel):
    """Cluster provider configuration."""
    type: Literal["slurm", "local"] = Field(default="slurm", description="Cluster type")
    slurm: Optional[SlurmConfig] = Field(default=None, description="SLURM-specific config")
    ssh_host: Optional[str] = Field(default=None, description="SSH host for remote clusters")


class ModelConfig(BaseModel):
    """Model serving configuration."""
    name: str = Field(description="Model alias/short name")
    model_path: str = Field(description="HuggingFace model path or local path")
    base_port: int = Field(default=5900, description="Base port for this model")
    replicas: int = Field(default=1, description="Number of replicas to run")
    gpu_memory_utilization: float = Field(default=0.85, description="GPU memory utilization (0-1)")
    max_model_len: Optional[int] = Field(default=None, description="Maximum model context length")
    trust_remote_code: bool = Field(default=False, description="Trust remote code for custom models")


class Config(BaseModel):
    """Root configuration for agent-infra."""
    version: str = Field(default="1.0", description="Config schema version")
    proxy: ProxyConfig = Field(default_factory=ProxyConfig, description="Proxy configuration")
    cluster: ClusterConfig = Field(default_factory=ClusterConfig, description="Cluster configuration")
    models: list[ModelConfig] = Field(default_factory=list, description="Model configurations")
    headers: HeadersConfig = Field(default_factory=HeadersConfig, description="Header name configuration")

    def get_model(self, name: str) -> Optional[ModelConfig]:
        """Get model config by name."""
        for model in self.models:
            if model.name == name:
                return model
        return None

    def get_partition_preferences(self) -> list[dict]:
        """Get partition preferences in legacy format for compatibility."""
        if self.cluster.slurm is None:
            return []
        return [
            {
                "partition": p.name,
                "qos": p.qos,
                "gpus": p.gpus_per_node,
            }
            for p in sorted(self.cluster.slurm.partitions, key=lambda x: x.priority)
        ]
