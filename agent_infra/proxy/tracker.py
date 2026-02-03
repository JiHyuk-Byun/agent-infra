"""Request tracking for queue visibility and monitoring."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TrackedRequest:
    """A single tracked request in the queue."""
    request_id: str
    source: str  # Client IP or identifier
    model: str
    path: str
    submitted_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    backend: Optional[str] = None
    status: str = "pending"  # pending | in_flight | completed | failed
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    client_id: Optional[str] = None
    client_command: Optional[str] = None
    request_summary: Optional[str] = None   # last user message text (truncated)
    response_summary: Optional[str] = None  # LLM response text (truncated)
    backend_time_ms: Optional[float] = None  # pure backend HTTP roundtrip time
    agent_pre_ms: Optional[float] = None     # agent pre-request time (from timing header)
    agent_post_ms: Optional[float] = None    # agent post-request time (from timing header)
    turn_number: Optional[int] = None        # sequential turn number within session (1-indexed)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        now = time.time()
        result = {
            "request_id": self.request_id,
            "source": self.source,
            "model": self.model,
            "status": self.status,
            "backend": self.backend,
            "wait_time_ms": round(((self.started_at or now) - self.submitted_at) * 1000, 2),
        }
        if self.started_at and self.status == "in_flight":
            result["processing_time_ms"] = round((now - self.started_at) * 1000, 2)
        if self.completed_at:
            result["total_time_ms"] = round((self.completed_at - self.submitted_at) * 1000, 2)
        if self.session_id:
            result["session_id"] = self.session_id
            # Also include as episode_id for backward compatibility
            result["episode_id"] = self.session_id
        if self.task_id:
            result["task_id"] = self.task_id
            # Also include as instruction_id for backward compatibility
            result["instruction_id"] = self.task_id
        return result


class RequestTracker:
    """Track pending and in-flight requests for queue visibility."""

    def __init__(
        self,
        max_history: int = 1000,
        cleanup_interval: float = 60.0,
        stale_timeout: float = 600.0,
    ):
        self.requests: dict[str, TrackedRequest] = {}
        self.max_history = max_history
        self.cleanup_interval = cleanup_interval
        self.stale_timeout = stale_timeout  # max age for pending/in_flight before forced cleanup
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        # Per-session turn counters (never decremented, survives request cleanup)
        self._session_turn_counters: dict[str, int] = {}

    async def start(self):
        """Start background cleanup task."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        """Stop background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def _cleanup_loop(self):
        """Periodically clean up old completed requests."""
        while True:
            await asyncio.sleep(self.cleanup_interval)
            await self._cleanup_old_requests()

    async def _cleanup_old_requests(self):
        """Remove completed requests older than cleanup_interval and stale pending/in_flight."""
        async with self._lock:
            now = time.time()
            cutoff = now - self.cleanup_interval
            stale_cutoff = now - self.stale_timeout
            to_remove = []
            for rid, req in self.requests.items():
                if req.status in ("completed", "failed") and req.completed_at and req.completed_at < cutoff:
                    to_remove.append(rid)
                elif req.status in ("pending", "in_flight") and req.submitted_at < stale_cutoff:
                    # Force-expire stuck requests
                    to_remove.append(rid)
            for rid in to_remove:
                del self.requests[rid]

            # Also enforce max_history by removing oldest completed
            if len(self.requests) > self.max_history:
                completed = [
                    (rid, req) for rid, req in self.requests.items()
                    if req.status in ("completed", "failed")
                ]
                completed.sort(key=lambda x: x[1].completed_at or 0)
                excess = len(self.requests) - self.max_history
                for rid, _ in completed[:excess]:
                    del self.requests[rid]

    async def submit(
        self,
        request_id: str,
        source: str,
        model: str,
        path: str,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_command: Optional[str] = None,
    ) -> TrackedRequest:
        """Register a new pending request."""
        async with self._lock:
            # Assign sequential turn number within session
            turn_number = None
            if session_id:
                counter = self._session_turn_counters.get(session_id, 0) + 1
                self._session_turn_counters[session_id] = counter
                turn_number = counter

            req = TrackedRequest(
                request_id=request_id,
                source=source,
                model=model,
                path=path,
                submitted_at=time.time(),
                status="pending",
                session_id=session_id,
                task_id=task_id,
                client_id=client_id,
                client_command=client_command,
                turn_number=turn_number,
            )
            self.requests[request_id] = req
            return req

    async def start_processing(self, request_id: str, backend: str):
        """Mark request as being processed by a backend."""
        async with self._lock:
            if request_id in self.requests:
                req = self.requests[request_id]
                req.started_at = time.time()
                req.backend = backend
                req.status = "in_flight"

    async def complete(self, request_id: str, success: bool = True):
        """Mark request as completed."""
        async with self._lock:
            if request_id in self.requests:
                req = self.requests[request_id]
                req.completed_at = time.time()
                req.status = "completed" if success else "failed"

    def get_status(self) -> dict[str, Any]:
        """Get current queue status."""
        now = time.time()
        pending = []
        in_flight = []
        recent_completed = []

        for req in self.requests.values():
            if req.status == "pending":
                pending.append(req)
            elif req.status == "in_flight":
                in_flight.append(req)
            elif req.status in ("completed", "failed"):
                # Only include recently completed (last 60s)
                if req.completed_at and now - req.completed_at < 60:
                    recent_completed.append(req)

        # Sort by submission time
        pending.sort(key=lambda r: r.submitted_at)
        in_flight.sort(key=lambda r: r.submitted_at)

        # Build sessions grouping (also aliased as episodes for backward compat)
        session_map: dict[str, dict[str, Any]] = {}
        for req in self.requests.values():
            sid = req.session_id
            if not sid:
                continue
            if sid not in session_map:
                session_map[sid] = {
                    "session_id": sid,
                    "episode_id": sid,  # backward compat
                    "task_id": req.task_id or "",
                    "instruction_id": req.task_id or "",  # backward compat
                    "model": req.model,
                    "source": req.source,
                    "total_requests": 0,
                    "completed_requests": 0,
                    "pending_requests": 0,
                    "in_flight_requests": 0,
                    "failed_requests": 0,
                    "completed_turns": [],
                    # Total turns ever assigned (survives cleanup)
                    "total_turns": self._session_turn_counters.get(sid, 0),
                }
            sess = session_map[sid]
            sess["total_requests"] += 1
            if req.status == "completed":
                sess["completed_requests"] += 1
                if req.completed_at and req.started_at:
                    turn_data = {
                        "request_id": req.request_id,
                        "backend": req.backend,
                        "request_summary": req.request_summary,
                        "response_summary": req.response_summary,
                        "submitted_at": req.submitted_at,
                        "completed_at": req.completed_at,
                        "total_time_ms": round((req.completed_at - req.submitted_at) * 1000, 2),
                        "wait_time_ms": round((req.started_at - req.submitted_at) * 1000, 2),
                        "processing_time_ms": round((req.completed_at - req.started_at) * 1000, 2),
                        "turn_number": req.turn_number,
                    }
                    if req.backend_time_ms is not None:
                        turn_data["backend_time_ms"] = round(req.backend_time_ms, 2)
                    if req.agent_pre_ms is not None:
                        turn_data["agent_obs_ms"] = req.agent_pre_ms  # backward compat
                    if req.agent_post_ms is not None:
                        turn_data["agent_act_ms"] = req.agent_post_ms  # backward compat
                    sess["completed_turns"].append(turn_data)
            elif req.status == "pending":
                sess["pending_requests"] += 1
            elif req.status == "in_flight":
                sess["in_flight_requests"] += 1
            elif req.status == "failed":
                sess["failed_requests"] += 1

        # Sort completed_turns by submission time
        for sess in session_map.values():
            sess["completed_turns"].sort(key=lambda t: t["submitted_at"])

        # Build client grouping (also aliased as processes for backward compat)
        client_map: dict[str, dict[str, Any]] = {}
        session_to_client: dict[str, str] = {}
        for req in self.requests.values():
            cid = req.client_id
            sid = req.session_id
            if not cid or not sid:
                continue
            if cid not in client_map:
                client_map[cid] = {
                    "client_id": cid,
                    "process_id": cid,  # backward compat
                    "client_command": req.client_command or "",
                    "process_command": req.client_command or "",  # backward compat
                    "session_ids": set(),
                }
            client_map[cid]["session_ids"].add(sid)
            session_to_client[sid] = cid

        # Build clients list with embedded sessions
        clients = []
        for cid, cinfo in client_map.items():
            sessions = [session_map[sid] for sid in cinfo["session_ids"] if sid in session_map]
            clients.append({
                "client_id": cinfo["client_id"],
                "process_id": cinfo["process_id"],  # backward compat
                "client_command": cinfo["client_command"],
                "process_command": cinfo["process_command"],  # backward compat
                "sessions": sessions,
                "episodes": sessions,  # backward compat
            })

        # Orphan sessions: have session_id but no client_id
        orphan_session_ids = set(session_map.keys()) - set(session_to_client.keys())
        orphan_sessions = [session_map[sid] for sid in orphan_session_ids]

        return {
            "summary": {
                "pending": len(pending),
                "in_flight": len(in_flight),
                "completed_last_minute": len(recent_completed),
                "total_tracked": len(self.requests),
            },
            "pending": [r.to_dict() for r in pending[:50]],  # Limit to 50
            "in_flight": [r.to_dict() for r in in_flight[:50]],
            "sessions": list(session_map.values()),
            "episodes": list(session_map.values()),  # backward compat
            "clients": clients,
            "processes": clients,  # backward compat
            "orphan_sessions": orphan_sessions,
            "orphan_episodes": orphan_sessions,  # backward compat
        }
