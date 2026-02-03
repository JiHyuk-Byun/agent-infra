"""Load-balancing reverse proxy for LLM backends.

Features:
- Multiple load balancing strategies (round_robin, least_connections, least_latency, least_load)
- Health checks with automatic failover
- OpenAI-compatible API passthrough
- Request tracking and metrics
- Configurable header names for session/task tracking
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
import threading
import uuid
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Optional

from aiohttp import web, ClientSession, ClientTimeout, ClientError, TCPConnector

from agent_infra.proxy.backend import Backend, BackendPool
from agent_infra.proxy.tracker import RequestTracker
from agent_infra.config.schema import HeadersConfig

# Use uvloop for better performance if available
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass


LOAD_CACHE_TTL = 1.0  # 1 second cache for GPU load metrics


def _get_local_ip() -> str:
    """Get the local IP address accessible from the network."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def log_route(method: str, path: str):
    """Log route access to stderr (unbuffered)."""
    sys.stderr.write(f"[PROXY] {method} {path}\n")
    sys.stderr.flush()


def parse_vllm_metrics(text: str) -> dict:
    """Parse vLLM Prometheus metrics."""
    metrics = {}
    for line in text.split('\n'):
        if line.startswith('vllm:num_requests_running'):
            try:
                metrics['running'] = int(float(line.split()[-1]))
            except (ValueError, IndexError):
                pass
        elif line.startswith('vllm:num_requests_waiting'):
            try:
                metrics['waiting'] = int(float(line.split()[-1]))
            except (ValueError, IndexError):
                pass
    return metrics


class LoadBalancingProxy:
    """Async reverse proxy with load balancing."""

    def __init__(
        self,
        port: int = 8000,
        health_check_interval: int = 30,
        request_timeout: int = 300,
        strategy: str = "least_load",
        verbose: bool = True,
        headers_config: Optional[HeadersConfig] = None,
    ):
        self.port = port
        self.health_check_interval = health_check_interval
        self.request_timeout = request_timeout
        self.strategy = strategy
        self.verbose = verbose
        self.headers = headers_config or HeadersConfig()

        self.pools: dict[str, BackendPool] = {}
        self.model_to_pool: dict[str, str] = {}  # full model name -> pool name
        self._cached_models: list[dict[str, Any]] = []
        self._models_cache_time: float = 0
        self._models_cache_ttl: float = 30.0  # refresh model list every 30s
        self.session: Optional[ClientSession] = None
        self.app = web.Application(client_max_size=0)  # No body size limit
        self._setup_routes()

        # Metrics
        self.total_requests = 0
        self.total_errors = 0
        self.start_time = time.time()

        # Request tracking
        self.tracker = RequestTracker()

        # Background task references
        self._background_tasks: list[asyncio.Task] = []

    def add_backend(self, model: str, host: str, port: int, partition: str = ""):
        """Add a backend for a model."""
        if model not in self.pools:
            self.pools[model] = BackendPool(model=model)
        self.pools[model].add_backend(host, port, partition=partition)

    def remove_backend(self, host: str, port: int) -> bool:
        """Remove a backend from all pools. Returns True if removed from any pool."""
        removed = False
        for pool in self.pools.values():
            if pool.remove_backend(host, port):
                removed = True
        return removed

    def _setup_routes(self):
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_get("/stats", self._handle_stats)
        self.app.router.add_get("/queue/status", self._handle_queue_status)
        self.app.router.add_get("/v1/models", self._handle_models)
        # Proxy routes (must be last due to wildcards)
        self.app.router.add_route("*", "/v1/{path:.*}", self._handle_proxy)
        self.app.router.add_route("*", "/{model}/v1/{path:.*}", self._handle_proxy_with_model)

    async def _handle_index(self, request: web.Request) -> web.Response:
        """List available models and endpoints."""
        log_route(request.method, "/")
        model_names = await self._fetch_model_names()

        data = {
            "models": model_names,
            "backends": {
                pool_name: [b.url for b in pool.backends]
                for pool_name, pool in self.pools.items()
            },
        }
        return web.json_response(data)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        log_route(request.method, "/health")
        healthy_models = sum(
            1 for pool in self.pools.values()
            if any(b.healthy for b in pool.backends)
        )
        status = "healthy" if healthy_models > 0 else "unhealthy"
        return web.json_response({
            "status": status,
            "healthy_models": healthy_models,
            "total_models": len(self.pools),
        })

    async def _fetch_backend_models(self, force: bool = False) -> list[dict[str, Any]]:
        """Fetch model info from all backends, deduplicated."""
        now = time.time()
        if not force and self._cached_models and now - self._models_cache_time < self._models_cache_ttl:
            return self._cached_models

        seen_models = set()
        all_models = []

        for pool_name, pool in self.pools.items():
            for backend in pool.backends:
                if not backend.healthy:
                    continue
                try:
                    async with self.session.get(
                        f"{backend.url}/v1/models",
                        timeout=ClientTimeout(total=5),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for model in data.get("data", []):
                                model_id = model.get("id")
                                if model_id:
                                    self.model_to_pool[model_id] = pool_name
                                    if model_id not in seen_models:
                                        seen_models.add(model_id)
                                        all_models.append(model)
                            break  # Got models from one backend in pool
                except Exception:
                    continue

        self._cached_models = all_models
        self._models_cache_time = now
        return all_models

    async def _fetch_model_names(self) -> list[str]:
        """Fetch model names from all backends (cached)."""
        models = await self._fetch_backend_models()
        return [m.get("id") for m in models if m.get("id")]

    async def _handle_models(self, request: web.Request) -> web.Response:
        """OpenAI-compatible /v1/models endpoint."""
        log_route(request.method, "/v1/models")
        models = await self._fetch_backend_models()
        return web.json_response({
            "object": "list",
            "data": models,
        })

    async def _handle_stats(self, request: web.Request) -> web.Response:
        """Return detailed statistics."""
        log_route(request.method, "/stats")
        uptime = time.time() - self.start_time
        model_names = await self._fetch_model_names()
        data = {
            "uptime_seconds": round(uptime, 2),
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "error_rate": round(self.total_errors / max(1, self.total_requests) * 100, 2),
            "requests_per_minute": round(self.total_requests / max(1, uptime / 60), 2),
            "strategy": self.strategy,
            "models": model_names,
            "pools": [pool.get_stats() for pool in self.pools.values()],
        }
        return web.json_response(data)

    async def _handle_queue_status(self, request: web.Request) -> web.Response:
        """Return current queue status with pending and in-flight requests."""
        log_route(request.method, "/queue/status")
        queue_status = self.tracker.get_status()

        # Add backend load info
        backends_load = []
        for pool in self.pools.values():
            for b in pool.backends:
                backends_load.append({
                    "url": b.url,
                    "healthy": b.healthy,
                    "gpu_load": b.gpu_load,
                    "inflight": b.inflight,
                    "avg_latency_ms": round(b.avg_latency_ms, 2),
                    "partition": b.partition,
                })

        queue_status["backends"] = backends_load
        return web.json_response(queue_status)

    async def _handle_proxy_with_model(self, request: web.Request) -> web.Response:
        """Handle requests with model in URL path: /{model}/v1/..."""
        model = request.match_info["model"]
        path = request.match_info["path"]
        log_route(request.method, f"/{model}/v1/{path}")
        body = await request.read()
        return await self._proxy_request(request, model, f"/v1/{path}", body=body)

    async def _handle_proxy(self, request: web.Request) -> web.Response:
        """Handle requests to /v1/... - extract model from body."""
        path = "/v1/" + request.match_info["path"]
        log_route(request.method, path)

        body = await request.read()

        # Try to get model from request body
        model = None
        if body:
            try:
                body_json = json.loads(body)
                model = body_json.get("model")
            except Exception:
                pass

        # Fallback: use first available model or check query param
        if not model:
            model = request.query.get("model")
        if not model and self.pools:
            model = list(self.pools.keys())[0]

        return await self._proxy_request(request, model, path, body=body)

    def _extract_headers(self, request: web.Request) -> dict[str, Optional[str]]:
        """Extract tracking headers using configured header names."""
        h = self.headers
        return {
            "session_id": request.headers.get(h.session),
            "task_id": request.headers.get(h.task),
            "client_id": request.headers.get(h.client),
            "timing_pre": request.headers.get(h.timing_pre),
            "timing_post": request.headers.get(h.timing_post),
        }

    async def _proxy_request(
        self, request: web.Request, model: str, path: str, body: bytes = None
    ) -> web.Response:
        """Proxy request to appropriate backend."""
        self.total_requests += 1

        # Generate request ID and extract tracking headers
        request_id = uuid.uuid4().hex[:16]
        source = request.remote or "unknown"
        headers_data = self._extract_headers(request)

        # Also check legacy header names for backward compatibility
        session_id = headers_data["session_id"] or request.headers.get("X-Episode-ID")
        task_id = headers_data["task_id"] or request.headers.get("X-Instruction-ID")
        client_id = headers_data["client_id"] or request.headers.get("X-Process-ID")
        client_command = request.headers.get("X-Process-Command")

        await self.tracker.submit(
            request_id, source, model or "unknown", path,
            session_id=session_id, task_id=task_id,
            client_id=client_id, client_command=client_command,
        )

        # Find backend pool
        pool = self.pools.get(model)

        if not pool:
            pool_name = self.model_to_pool.get(model)
            if pool_name:
                pool = self.pools.get(pool_name)

        if not pool:
            await self._fetch_backend_models(force=True)
            pool_name = self.model_to_pool.get(model)
            if pool_name:
                pool = self.pools.get(pool_name)

        if not pool:
            # Try case-insensitive partial match
            model_lower = model.lower() if model else ""
            for name, p in self.pools.items():
                if model_lower and (model_lower in name.lower() or name.lower() in model_lower):
                    pool = p
                    break

        if not pool:
            self.total_errors += 1
            await self.tracker.complete(request_id, success=False)
            available = list(self.model_to_pool.keys()) or list(self.pools.keys())
            return web.json_response(
                {"error": f"No backend for model: {model}", "available": available},
                status=404,
            )

        # Get backend
        backend = await pool.get_backend(self.strategy)
        if not backend:
            self.total_errors += 1
            await self.tracker.complete(request_id, success=False)
            return web.json_response(
                {"error": f"No healthy backends for model: {model}"},
                status=503,
            )

        # Mark as in-flight
        await self.tracker.start_processing(request_id, backend.url)

        if self.verbose:
            print(f"[{request_id}] {request.method} {path} -> {backend.url}")

        # Proxy the request
        start_time = time.time()
        try:
            if body is None:
                body = await request.read()

            # Capture request summary
            try:
                body_json = json.loads(body) if body else {}
                messages = body_json.get("messages", [])
                last_user_msg = ""
                for msg in reversed(messages):
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                            last_user_msg = " ".join(text_parts)
                        else:
                            last_user_msg = str(content)
                        break
                if last_user_msg and request_id in self.tracker.requests:
                    self.tracker.requests[request_id].request_summary = last_user_msg[:200]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

            # Forward headers
            forward_headers = {
                k: v for k, v in request.headers.items()
                if k.lower() not in ("host", "content-length", "transfer-encoding")
            }

            # Extract and store agent timing headers
            if request_id in self.tracker.requests:
                timing_pre = headers_data["timing_pre"] or request.headers.get("X-Timing-Obs-Ms")
                timing_post = headers_data["timing_post"] or request.headers.get("X-Timing-Act-Ms")
                if timing_pre:
                    try:
                        self.tracker.requests[request_id].agent_pre_ms = float(timing_pre)
                    except (ValueError, TypeError):
                        pass
                if timing_post:
                    try:
                        self.tracker.requests[request_id].agent_post_ms = float(timing_post)
                    except (ValueError, TypeError):
                        pass

            # Make request to backend
            url = f"{backend.url}{path}"
            timeout = ClientTimeout(total=self.request_timeout)

            backend_start = time.time()
            async with self.session.request(
                method=request.method,
                url=url,
                headers=forward_headers,
                data=body,
                timeout=timeout,
            ) as resp:
                response_body = await resp.read()
                backend_elapsed_ms = (time.time() - backend_start) * 1000
                latency_ms = (time.time() - start_time) * 1000

                if request_id in self.tracker.requests:
                    self.tracker.requests[request_id].backend_time_ms = round(backend_elapsed_ms, 2)

                is_success = resp.status < 500
                backend.record_request(latency_ms, is_success)
                backend.consecutive_timeouts = 0

                if not is_success:
                    self.total_errors += 1

                # Capture response summary
                try:
                    resp_json = json.loads(response_body)
                    choices = resp_json.get("choices", [])
                    if choices:
                        action_text = choices[0].get("message", {}).get("content", "")
                        if action_text and request_id in self.tracker.requests:
                            self.tracker.requests[request_id].response_summary = action_text[:200]
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

                await self.tracker.complete(request_id, success=is_success)

                if self.verbose:
                    print(f"[{request_id}] <- {resp.status} ({latency_ms:.0f}ms)")

                return web.Response(
                    status=resp.status,
                    headers={
                        k: v for k, v in resp.headers.items()
                        if k.lower() not in ("content-encoding", "transfer-encoding", "content-length")
                    },
                    body=response_body,
                )

        except asyncio.TimeoutError:
            latency_ms = (time.time() - start_time) * 1000
            backend.record_request(latency_ms, False)
            backend.consecutive_timeouts += 1
            if backend.consecutive_timeouts >= 3:
                backend.healthy = False
                if self.verbose:
                    print(f"[{request_id}] Backend {backend.url} marked unhealthy after {backend.consecutive_timeouts} consecutive timeouts")
            self.total_errors += 1
            await self.tracker.complete(request_id, success=False)
            if self.verbose:
                print(f"[{request_id}] <- TIMEOUT ({latency_ms:.0f}ms)")
            return web.json_response(
                {"error": "Backend timeout", "backend": backend.url},
                status=504,
            )
        except ClientError as e:
            latency_ms = (time.time() - start_time) * 1000
            backend.record_request(latency_ms, False)
            backend.healthy = False
            await self.tracker.complete(request_id, success=False)
            if self.verbose:
                print(f"[{request_id}] <- ERROR: {e} ({latency_ms:.0f}ms)")
            self.total_errors += 1
            return web.json_response(
                {"error": f"Backend error: {str(e)}", "backend": backend.url},
                status=502,
            )
        finally:
            backend.inflight = max(0, backend.inflight - 1)

    async def _resilient_loop(self, loop_fn, name: str):
        """Run a loop function, restarting on unexpected exceptions."""
        while True:
            try:
                await loop_fn()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[ERROR] Background loop '{name}' crashed: {e}, restarting...")
                await asyncio.sleep(1)

    async def _health_check_loop(self):
        """Periodically check backend health."""
        while True:
            await asyncio.sleep(self.health_check_interval)
            await self._check_all_backends()

    async def _gpu_load_refresh_loop(self):
        """Background loop that refreshes GPU load metrics."""
        while True:
            await asyncio.sleep(LOAD_CACHE_TTL)
            for pool in self.pools.values():
                tasks = [
                    self._refresh_backend_load(b)
                    for b in pool.backends
                    if b.healthy
                ]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

    async def _refresh_backend_load(self, backend: Backend):
        """Fetch GPU load from vLLM /metrics endpoint."""
        now = time.time()
        if now - backend.load_last_updated < LOAD_CACHE_TTL:
            return

        try:
            async with self.session.get(
                f"{backend.url}/metrics",
                timeout=ClientTimeout(total=2),
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    metrics = parse_vllm_metrics(text)
                    backend.gpu_load = metrics.get('running', 0) + metrics.get('waiting', 0)
                    backend.load_last_updated = now
        except Exception:
            pass

    async def _check_all_backends(self):
        """Check health of all backends in parallel."""
        all_backends = [
            b for pool in self.pools.values() for b in pool.backends
        ]
        if all_backends:
            await asyncio.gather(
                *(self._check_single_backend(b) for b in all_backends),
                return_exceptions=True,
            )

    async def _check_single_backend(self, backend: Backend):
        """Check health of a single backend and refresh GPU load."""
        try:
            timeout = ClientTimeout(total=5)
            async with self.session.get(
                f"{backend.url}/health",
                timeout=timeout,
            ) as resp:
                was_unhealthy = not backend.healthy
                backend.healthy = resp.status < 500
                backend.last_check = time.time()
                if backend.healthy:
                    await self._refresh_backend_load(backend)
                    if was_unhealthy and self.verbose:
                        print(f"[HEALTH] Backend {backend.url} recovered")
        except Exception:
            backend.healthy = False
            backend.last_check = time.time()

    async def start(self):
        """Start the proxy server."""
        connector = TCPConnector(
            limit=0,
            limit_per_host=0,
            keepalive_timeout=60,
            enable_cleanup_closed=True,
        )
        self.session = ClientSession(connector=connector)

        # Initial health check
        await self._check_all_backends()

        # Fetch model names from backends
        await self._fetch_backend_models()

        # Start background loops
        self._background_tasks = [
            asyncio.create_task(self._resilient_loop(self._health_check_loop, "health_check")),
            asyncio.create_task(self._resilient_loop(self._gpu_load_refresh_loop, "gpu_load_refresh")),
        ]

        # Start request tracker
        await self.tracker.start()

        # Start web server
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()

        local_ip = _get_local_ip()
        hostname = socket.gethostname()

        print(f"Load balancing proxy running on http://0.0.0.0:{self.port}")
        print(f"Strategy: {self.strategy}")
        print(f"")
        print(f"  Local:   http://localhost:{self.port}")
        print(f"  Network: http://{local_ip}:{self.port}")
        print(f"  Host:    http://{hostname}:{self.port}")
        print(f"")
        print(f"  Agents: export OPENAI_BASE_URL=http://{local_ip}:{self.port}/v1")
        print(f"")
        print(f"Backend pools: {list(self.pools.keys())}")
        print(f"Available models: {list(self.model_to_pool.keys())}")
        for pool_name, pool in self.pools.items():
            print(f"  {pool_name}: {[b.url for b in pool.backends]}")

    async def stop(self):
        """Stop the proxy server."""
        for task in self._background_tasks:
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        await self.tracker.stop()
        if self.session:
            await self.session.close()


@contextmanager
def proxy_server(
    backends: dict[str, list[tuple]],
    port: int = 8000,
    strategy: str = "least_load",
    headers_config: Optional[HeadersConfig] = None,
):
    """Context manager for running the proxy server in a background thread.

    Args:
        backends: Dict mapping model name to list of (host, port[, partition]) tuples
        port: Port to run proxy on
        strategy: Load balancing strategy
        headers_config: Custom header names configuration

    Yields:
        LoadBalancingProxy instance (for dynamic backend management)

    Usage:
        backends = {
            "my_model": [("localhost", 5900, "gpu-partition"), ("localhost", 5910)],
        }
        with proxy_server(backends, port=8000) as proxy:
            proxy.add_backend("new_model", "localhost", 5920)
            input("Press Enter to stop")
    """
    proxy = LoadBalancingProxy(
        port=port,
        strategy=strategy,
        headers_config=headers_config,
    )
    for model, endpoints in backends.items():
        for endpoint in endpoints:
            if len(endpoint) >= 3:
                host, backend_port, partition = endpoint[0], endpoint[1], endpoint[2]
            else:
                host, backend_port = endpoint[0], endpoint[1]
                partition = ""
            proxy.add_backend(model, host, backend_port, partition=partition)

    loop = asyncio.new_event_loop()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(proxy.start())
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    # Wait for server to start
    time.sleep(0.5)

    try:
        yield proxy
    finally:
        loop.call_soon_threadsafe(loop.stop)


def parse_backends(backend_strs: list[str]) -> dict[str, list[tuple]]:
    """Parse backend strings like 'model=host:port,host:port'."""
    backends = defaultdict(list)
    for s in backend_strs:
        model, endpoints_str = s.split("=", 1)
        for endpoint in endpoints_str.split(","):
            host, port = endpoint.rsplit(":", 1)
            backends[model].append((host, int(port)))
    return dict(backends)
