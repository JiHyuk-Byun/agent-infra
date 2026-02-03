"""Command-line interface for agent-infra."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Literal, Optional

import tyro
from dataclasses import dataclass

from agent_infra.config import load_config, load_config_or_default


@dataclass
class StartArgs:
    """Start GPU server jobs on the cluster."""
    config: str = "config.yaml"
    """Path to config file"""
    models: tuple[str, ...] = ()
    """Specific models to start (empty = all models in config)"""
    replicas: int = 1
    """Number of replicas per model"""


@dataclass
class ConnectArgs:
    """Connect to running GPU backends with load-balancing proxy."""
    config: str = "config.yaml"
    """Path to config file"""
    background: bool = False
    """Run in background (daemon mode)"""
    poll_interval: int = 30
    """Seconds between SLURM polling for new GPU servers (0 to disable)"""


@dataclass
class StopArgs:
    """Stop a background proxy process."""
    config: str = "config.yaml"
    """Path to config file"""


@dataclass
class ProxyArgs:
    """Run the load-balancing proxy standalone."""
    port: int = 5800
    """Proxy port"""
    backends: tuple[str, ...] = ()
    """Backend specs: 'model=host:port,host:port'"""
    strategy: Literal["round_robin", "least_connections", "least_latency", "least_load"] = "least_load"
    """Load balancing strategy"""
    health_check_interval: int = 30
    """Health check interval in seconds"""
    request_timeout: int = 300
    """Request timeout in seconds"""


@dataclass
class StatusArgs:
    """Show status of running jobs and proxy."""
    config: str = "config.yaml"
    """Path to config file"""


def cmd_start(args: StartArgs):
    """Execute start command."""
    config = load_config(args.config)

    from agent_infra.orchestrator import ConnectionManager
    manager = ConnectionManager(config)

    models = list(args.models) if args.models else None
    manager.start_jobs(models=models, replicas=args.replicas)


def cmd_connect(args: ConnectArgs):
    """Execute connect command."""
    config = load_config(args.config)

    from agent_infra.orchestrator import ConnectionManager
    manager = ConnectionManager(config)

    manager.connect(background=args.background, poll_interval=args.poll_interval)


def cmd_stop(args: StopArgs):
    """Execute stop command."""
    config = load_config_or_default(args.config)

    from agent_infra.orchestrator import ConnectionManager
    manager = ConnectionManager(config)

    manager.stop()


def cmd_proxy(args: ProxyArgs):
    """Execute proxy command."""
    from agent_infra.proxy.server import LoadBalancingProxy, parse_backends

    backends = {}
    if args.backends:
        backends = parse_backends(list(args.backends))

    proxy = LoadBalancingProxy(
        port=args.port,
        strategy=args.strategy,
        health_check_interval=args.health_check_interval,
        request_timeout=args.request_timeout,
    )

    for model, endpoints in backends.items():
        for host, port in endpoints:
            proxy.add_backend(model, host, port)

    async def run():
        await proxy.start()
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await proxy.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nShutting down...")


def cmd_status(args: StatusArgs):
    """Execute status command."""
    from agent_infra.cluster import get_slurm_jobs

    print("GPU Jobs:")
    print("-" * 60)

    jobs = get_slurm_jobs()
    if jobs:
        for job_id, info in jobs.items():
            node = info.get("node", "?")
            partition = info.get("partition", "?")
            command = info.get("command", "?")
            print(f"  {job_id}: {node} ({partition})")
            if command:
                print(f"    Command: {command[:60]}...")
    else:
        print("  No SLURM jobs running")

    print()


def main():
    """Main entry point for agent-infra CLI."""
    parser = argparse.ArgumentParser(
        description="GPU serving infrastructure for multi-turn LLM agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start GPU jobs on SLURM
  agent-infra start --config config.yaml --replicas 2

  # Connect to running jobs with proxy
  agent-infra connect --config config.yaml

  # Run proxy standalone
  agent-infra proxy --port 5800 --backends "my_model=localhost:5900,localhost:5910"

  # Check status
  agent-infra status
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # start command
    start_parser = subparsers.add_parser("start", help="Start GPU server jobs")
    start_parser.add_argument("--config", default="config.yaml", help="Config file path")
    start_parser.add_argument("--models", nargs="*", help="Models to start")
    start_parser.add_argument("--replicas", type=int, default=1, help="Replicas per model")

    # connect command
    connect_parser = subparsers.add_parser("connect", help="Connect proxy to GPU backends")
    connect_parser.add_argument("--config", default="config.yaml", help="Config file path")
    connect_parser.add_argument("--background", action="store_true", help="Run in background")
    connect_parser.add_argument("--poll-interval", type=int, default=30, help="SLURM poll interval")

    # stop command
    stop_parser = subparsers.add_parser("stop", help="Stop background proxy")
    stop_parser.add_argument("--config", default="config.yaml", help="Config file path")

    # proxy command
    proxy_parser = subparsers.add_parser("proxy", help="Run proxy standalone")
    proxy_parser.add_argument("--port", type=int, default=5800, help="Proxy port")
    proxy_parser.add_argument("--backends", nargs="*", help="Backend specs: model=host:port,...")
    proxy_parser.add_argument(
        "--strategy",
        choices=["round_robin", "least_connections", "least_latency", "least_load"],
        default="least_load",
        help="Load balancing strategy",
    )
    proxy_parser.add_argument("--health-check-interval", type=int, default=30)
    proxy_parser.add_argument("--request-timeout", type=int, default=300)

    # status command
    status_parser = subparsers.add_parser("status", help="Show status")
    status_parser.add_argument("--config", default="config.yaml", help="Config file path")

    args = parser.parse_args()

    if args.command == "start":
        cmd_start(StartArgs(
            config=args.config,
            models=tuple(args.models or []),
            replicas=args.replicas,
        ))
    elif args.command == "connect":
        cmd_connect(ConnectArgs(
            config=args.config,
            background=args.background,
            poll_interval=args.poll_interval,
        ))
    elif args.command == "stop":
        cmd_stop(StopArgs(config=args.config))
    elif args.command == "proxy":
        cmd_proxy(ProxyArgs(
            port=args.port,
            backends=tuple(args.backends or []),
            strategy=args.strategy,
            health_check_interval=args.health_check_interval,
            request_timeout=args.request_timeout,
        ))
    elif args.command == "status":
        cmd_status(StatusArgs(config=args.config))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
