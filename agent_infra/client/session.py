"""Session context for agent-proxy integration."""

from __future__ import annotations

import os
import socket
from typing import Optional


class SessionContext:
    """Session context for tracking multi-turn agent interactions.

    This class helps agents communicate session/task metadata to the proxy
    via HTTP headers. The proxy uses these headers to:
    - Group requests by session for monitoring
    - Track turn numbers within sessions
    - Analyze timing breakdowns (observation, inference, action)

    Usage:
        from agent_infra.client import SessionContext
        from openai import OpenAI

        client = OpenAI(base_url="http://localhost:5800/v1", api_key="not-needed")
        ctx = SessionContext(session_id="my-session-001", task_id="summarize")

        for turn in range(10):
            # Record observation build time
            ctx.set_timing(pre_ms=150.0)

            # Make LLM call with tracking headers
            response = client.chat.completions.create(
                model="my_model",
                messages=[...],
                extra_headers=ctx.get_headers(),
            )

            # Execute action and record timing
            execute_action(response)
            ctx.set_timing(post_ms=200.0)
    """

    # Default header names (can be customized for different projects)
    DEFAULT_HEADERS = {
        "session": "X-Session-ID",
        "task": "X-Task-ID",
        "client": "X-Client-ID",
        "timing_pre": "X-Timing-Pre-Ms",
        "timing_post": "X-Timing-Post-Ms",
    }

    def __init__(
        self,
        session_id: str,
        task_id: Optional[str] = None,
        headers_config: Optional[dict[str, str]] = None,
    ):
        """Initialize session context.

        Args:
            session_id: Unique identifier for this session/episode
            task_id: Optional task/instruction identifier
            headers_config: Custom header names (overrides defaults)
        """
        self.session_id = session_id
        self.task_id = task_id
        self.headers_config = {**self.DEFAULT_HEADERS, **(headers_config or {})}
        self._timing: dict[str, float] = {}
        self._client_id = f"{socket.gethostname()}:{os.getpid()}"

    def set_timing(
        self,
        pre_ms: Optional[float] = None,
        post_ms: Optional[float] = None,
    ):
        """Record timing for the current turn.

        These values are sent as headers on the next LLM call and help
        the proxy analyze where time is spent in the agent pipeline:

        - pre_ms: Time spent building the observation before LLM call
                  (e.g., taking screenshots, building AX tree)
        - post_ms: Time spent executing the action after LLM response
                   (e.g., clicking buttons, typing text)

        Args:
            pre_ms: Pre-request time in milliseconds
            post_ms: Post-request time in milliseconds
        """
        if pre_ms is not None:
            self._timing["pre"] = pre_ms
        if post_ms is not None:
            self._timing["post"] = post_ms

    def get_headers(self) -> dict[str, str]:
        """Get headers to pass to the LLM client.

        Returns:
            Dict of headers for use with OpenAI SDK's extra_headers parameter

        Example:
            response = client.chat.completions.create(
                model="my_model",
                messages=[...],
                extra_headers=ctx.get_headers(),
            )
        """
        h = self.headers_config
        headers = {
            h["session"]: self.session_id,
            h["client"]: self._client_id,
        }

        if self.task_id:
            headers[h["task"]] = self.task_id

        # Include and clear timing values
        if "pre" in self._timing:
            headers[h["timing_pre"]] = str(int(self._timing.pop("pre")))
        if "post" in self._timing:
            headers[h["timing_post"]] = str(int(self._timing.pop("post")))

        return headers

    def with_task(self, task_id: str) -> "SessionContext":
        """Create a new context with a different task ID.

        Useful when running multiple tasks within the same session.

        Args:
            task_id: New task identifier

        Returns:
            New SessionContext with updated task_id
        """
        return SessionContext(
            session_id=self.session_id,
            task_id=task_id,
            headers_config=self.headers_config,
        )

    @classmethod
    def from_config(
        cls,
        session_id: str,
        task_id: Optional[str] = None,
        config: Optional["Config"] = None,
    ) -> "SessionContext":
        """Create context from agent-infra config.

        Args:
            session_id: Session identifier
            task_id: Task identifier
            config: Agent-infra Config object

        Returns:
            SessionContext with header names from config
        """
        if config is None:
            return cls(session_id=session_id, task_id=task_id)

        headers_config = {
            "session": config.headers.session,
            "task": config.headers.task,
            "client": config.headers.client,
            "timing_pre": config.headers.timing_pre,
            "timing_post": config.headers.timing_post,
        }
        return cls(
            session_id=session_id,
            task_id=task_id,
            headers_config=headers_config,
        )
