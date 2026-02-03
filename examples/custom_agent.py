#!/usr/bin/env python3
"""
Custom agent integration example - using SessionContext with OpenAI SDK.

This example shows how to integrate agent-infra session tracking
into your own agent implementation.

Usage:
    # First start the proxy
    agent-infra connect --config configs/example.yaml

    # Then run this script
    python examples/custom_agent.py
"""

import time
from agent_infra.client import SessionContext

# Simulated agent - replace with your actual agent logic


def build_observation():
    """Simulate building an observation (screenshot, AX tree, etc.)."""
    time.sleep(0.15)  # 150ms
    return {"page_content": "Hello World"}


def execute_action(action: str):
    """Simulate executing an action (click, type, etc.)."""
    time.sleep(0.2)  # 200ms
    print(f"  Executed: {action}")


def main():
    # Create OpenAI client pointing to the proxy
    # Note: openai package is optional - install if needed
    try:
        from openai import OpenAI
    except ImportError:
        print("This example requires the openai package:")
        print("  pip install openai")
        return

    client = OpenAI(
        base_url="http://localhost:5800/v1",
        api_key="not-needed",  # Local proxy doesn't need API key
    )

    # Create session context for tracking
    ctx = SessionContext(
        session_id="demo-session-001",
        task_id="example-task",
    )

    print("Starting multi-turn conversation...")
    print("=" * 60)

    messages = [
        {"role": "system", "content": "You are a helpful assistant."}
    ]

    for turn in range(3):
        print(f"\n--- Turn {turn + 1} ---")

        # Build observation and record timing
        start = time.time()
        obs = build_observation()
        obs_time_ms = (time.time() - start) * 1000
        ctx.set_timing(pre_ms=obs_time_ms)
        print(f"  Observation: {obs_time_ms:.0f}ms")

        # User message (in real agent, this comes from observation)
        messages.append({
            "role": "user",
            "content": f"Turn {turn + 1}: What can you help me with?"
        })

        # Make LLM call with tracking headers
        try:
            response = client.chat.completions.create(
                model="my_model",  # Use your model name
                messages=messages,
                max_tokens=100,
                extra_headers=ctx.get_headers(),  # Include tracking headers
            )

            assistant_msg = response.choices[0].message.content
            messages.append({"role": "assistant", "content": assistant_msg})
            print(f"  Assistant: {assistant_msg[:80]}...")

            # Execute action and record timing
            start = time.time()
            execute_action("click_button")
            act_time_ms = (time.time() - start) * 1000
            ctx.set_timing(post_ms=act_time_ms)
            print(f"  Action: {act_time_ms:.0f}ms")

        except Exception as e:
            print(f"  Error: {e}")
            print("  Make sure the proxy is running and has backends connected.")
            break

    print("\n" + "=" * 60)
    print("Session complete!")
    print(f"  Session ID: {ctx.session_id}")
    print(f"  Task ID: {ctx.task_id}")
    print("\nView timing analysis in the dashboard:")
    print("  cd dashboard && cargo run -- --proxy http://localhost:5800")


if __name__ == "__main__":
    main()
