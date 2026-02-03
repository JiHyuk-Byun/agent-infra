#!/usr/bin/env python3
"""
Basic proxy example - run a load-balancing proxy with hardcoded backends.

Usage:
    python examples/basic_proxy.py
"""

import asyncio
from agent_infra.proxy import LoadBalancingProxy


async def main():
    # Create proxy with custom settings
    proxy = LoadBalancingProxy(
        port=5800,
        strategy="least_load",
        health_check_interval=30,
        request_timeout=300,
    )

    # Add backends manually
    # In a real setup, these would be your vLLM servers
    proxy.add_backend("my_model", "localhost", 5900)
    proxy.add_backend("my_model", "localhost", 5910)

    # Start the proxy
    await proxy.start()

    print("\nProxy is running. Use Ctrl+C to stop.")
    print("Test with:")
    print('  curl http://localhost:5800/health')
    print('  curl http://localhost:5800/stats')

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await proxy.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down...")
