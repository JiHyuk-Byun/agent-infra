#!/usr/bin/env python3
"""
SLURM setup example - start GPU jobs and connect proxy.

Usage:
    python examples/slurm_setup.py
"""

from agent_infra import load_config, ConnectionManager


def main():
    # Load configuration
    config = load_config("configs/example.yaml")

    # Create connection manager
    manager = ConnectionManager(config)

    # Start GPU jobs
    print("=" * 60)
    print("Starting GPU jobs...")
    print("=" * 60)
    manager.start_jobs(
        models=["my_model"],
        replicas=2,
    )

    print()
    print("Jobs submitted. Wait for them to start, then run:")
    print("  python examples/slurm_setup.py --connect")


def connect():
    """Connect to running jobs with proxy."""
    config = load_config("configs/example.yaml")
    manager = ConnectionManager(config)

    print("=" * 60)
    print("Connecting to GPU backends...")
    print("=" * 60)
    manager.connect(
        background=False,
        poll_interval=30,
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--connect":
        connect()
    else:
        main()
