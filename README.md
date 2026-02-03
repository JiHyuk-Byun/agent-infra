# Agent Infra

Multi-turn LLM agent infrastructure for GPU serving. SLURM cluster integration, load-balancing proxy, and real-time TUI dashboard.

## Features

- **Load-Balancing Proxy**: 4 strategies (least_load, round_robin, least_connections, least_latency)
- **SLURM Integration**: GPU partition auto-allocation, job management
- **SSH Tunnels**: Automatic remote GPU node connections
- **Real-time Dashboard**: Rust TUI for pipeline monitoring
- **Session Tracking**: Per-request timing, bottleneck analysis

## Installation

```bash
# From source
git clone https://github.com/your-org/agent-infra.git
cd agent-infra
pip install -e .

# Dashboard (optional)
cd dashboard
cargo build --release
```

## Quickstart

### Step 1: Configuration

```bash
cp configs/example.yaml my-config.yaml
```

Edit `my-config.yaml`:

```yaml
proxy:
  port: 5800
  strategy: least_load

cluster:
  type: slurm
  slurm:
    partitions:
      - name: your_gpu_partition
        qos: your_qos
        gpus_per_node: 4
        priority: 1

models:
  - name: my_model
    model_path: "org/model-name"
    base_port: 5900
    replicas: 2
    gpu_memory_utilization: 0.85

headers:
  session: X-Session-ID
  task: X-Task-ID
```

### Step 2: Start GPU Servers (SLURM)

```bash
# Submit vLLM jobs to SLURM
agent-infra start --config my-config.yaml

# Check status
agent-infra status
```

### Step 3: Connect Proxy

```bash
# Create SSH tunnels + start proxy
agent-infra connect --config my-config.yaml
```

### Step 4: Use in Agent

```python
from agent_infra.client import SessionContext
from openai import OpenAI

# Connect to proxy
client = OpenAI(
    base_url="http://localhost:5800/v1",
    api_key="not-needed",
)

# Create session context
ctx = SessionContext(
    session_id="my-session-001",
    task_id="task-summarize",
)

# Multi-turn conversation
messages = [{"role": "user", "content": "Hello!"}]

for turn in range(10):
    # Optional: track agent timing
    ctx.set_timing(pre_ms=150.0)  # observation build time

    response = client.chat.completions.create(
        model="my_model",
        messages=messages,
        extra_headers=ctx.get_headers(),  # session tracking headers
    )

    messages.append({"role": "assistant", "content": response.choices[0].message.content})
    messages.append({"role": "user", "content": "Tell me more."})

    ctx.set_timing(post_ms=200.0)  # action execution time
```

### Step 5: Monitor with Dashboard

```bash
cd dashboard
cargo run --release -- --proxy http://localhost:5800
```

### Step 6: Stop

```bash
# Stop proxy (Ctrl+C)
# Cancel SLURM jobs
agent-infra stop --config my-config.yaml
```

## CLI Reference

```bash
# Full pipeline
agent-infra start --config config.yaml    # Submit GPU jobs
agent-infra connect --config config.yaml  # SSH tunnels + proxy
agent-infra status                        # Check job status
agent-infra stop --config config.yaml     # Cancel all jobs

# Individual components
agent-infra proxy --port 5800             # Proxy only
```

## Configuration Reference

### Proxy Settings

```yaml
proxy:
  port: 5800                    # Proxy listen port
  strategy: least_load          # round_robin | least_connections | least_latency | least_load
  health_check_interval: 30     # Backend health check interval (seconds)
  request_timeout: 300          # Request timeout (seconds)
```

### Cluster Settings

```yaml
cluster:
  type: slurm                   # slurm | local
  slurm:
    partitions:
      - name: gpu_partition     # SLURM partition name
        qos: default            # QOS
        gpus_per_node: 4        # GPUs per node
        priority: 1             # Selection priority
```

### Model Settings

```yaml
models:
  - name: my_model              # Model name for routing
    model_path: org/model-name  # HuggingFace model path
    base_port: 5900             # Starting port number
    replicas: 2                 # Number of replicas
    gpu_memory_utilization: 0.85
    tensor_parallel_size: 1     # GPUs per replica
```

### Header Settings

```yaml
headers:
  session: X-Session-ID        # Session ID header
  task: X-Task-ID              # Task ID header
  client: X-Client-ID          # Client ID header
  timing_pre: X-Timing-Pre-Ms  # Pre-request timing
  timing_post: X-Timing-Post-Ms # Post-request timing
```

## Dashboard

Real-time TUI dashboard showing:

- GPU backend health and load
- Request queue status
- Session/turn tracking
- Bottleneck analysis

```bash
cd dashboard
cargo run --release -- \
  --proxy http://localhost:5800 \
  --theme dark \
  --interval 2
```

### Dashboard Controls

| Key | Action |
|-----|--------|
| Tab | Switch panels |
| j/k or arrows | Navigate |
| Enter | Expand/collapse |
| q/Esc | Quit |

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Agent     │────▶│    Proxy     │────▶│  GPU Pool   │
│  (Client)   │     │ (Load Bal.)  │     │  (vLLM)     │
└─────────────┘     └──────────────┘     └─────────────┘
       │                   │                    │
       │           ┌──────────────┐            │
       └──────────▶│  Dashboard   │◀───────────┘
                   │   (TUI)      │
                   └──────────────┘
```

## Python API

### SessionContext

```python
from agent_infra.client import SessionContext

ctx = SessionContext(
    session_id="session-123",
    task_id="task-456",
    headers_config={...}  # Optional: custom header names
)

# Set timing for bottleneck analysis
ctx.set_timing(pre_ms=100.0, post_ms=200.0)

# Get headers for OpenAI client
headers = ctx.get_headers()
```

### ConnectionManager

```python
from agent_infra import load_config, ConnectionManager

config = load_config("config.yaml")
manager = ConnectionManager(config)

# Start GPU jobs + proxy
await manager.start()
await manager.connect()

# Stop
await manager.stop()
```

### LoadBalancingProxy

```python
from agent_infra.proxy import LoadBalancingProxy

proxy = LoadBalancingProxy(config)
await proxy.start()
```

## Development

```bash
# Python tests
pytest tests/

# Rust dashboard
cd dashboard
cargo test
cargo clippy
```

## License

MIT
