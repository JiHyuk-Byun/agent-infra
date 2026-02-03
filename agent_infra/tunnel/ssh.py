"""SSH tunnel management for remote GPU backends."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field


def kill_tunnel(port: int):
    """Kill any ssh process that forwards LOCAL port `port`."""
    pattern = f"ssh.*L {port}:"
    try:
        subprocess.run(
            ["pkill", "-f", pattern],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        print(f"[INFO] Killed existing tunnel(s) using port {port}")
    except Exception as e:
        print(f"[WARN] Failed to kill tunnel on port {port}: {e}")


@dataclass
class SSHTunnelManager:
    """Manage SSH tunnels using SSH config aliases.

    Example ~/.ssh/config:
        Host mycluster
            HostName login.cluster.edu
            User myname
            ProxyJump gateway.edu
            IdentityFile ~/.ssh/id_rsa

    Usage:
        endpoints = [("model_name", "node05", 5900)]
        with SSHTunnelManager(endpoints):
            # Tunnels are now active
            ...
        # Tunnels closed on exit
    """

    endpoints: list[tuple[str, str, int]]
    """List of (model_name, remote_host, port) tuples"""

    ssh_options: list[str] = field(
        default_factory=lambda: [
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "TCPKeepAlive=yes",
            "-o", "Compression=no",  # Faster for high bandwidth
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=/dev/null",
        ]
    )

    procs: dict[tuple[str, int], subprocess.Popen] = field(
        init=False, default_factory=dict
    )

    def _build_cmd(self, remote_host: str, remote_port: int) -> list[str]:
        """Build SSH command for local port forwarding."""
        local_port = remote_port  # Map local:remote directly
        return [
            "ssh",
            "-L", f"{local_port}:localhost:{remote_port}",
            "-N",
            "-f",
            *self.ssh_options,
            remote_host,
        ]

    def start(self):
        """Start all tunnels."""
        if self.procs:
            return

        for item in self.endpoints:
            # Handle both 3-tuple and 4-tuple formats
            model_name, remote_host, remote_port = item[:3]

            cmd = self._build_cmd(remote_host, remote_port)
            pretty = " ".join(shlex.quote(c) for c in cmd)
            kill_tunnel(remote_port)
            print(f"[SSH] Opening tunnel: localhost:{remote_port} -> {remote_host}:{remote_port}")
            print(f"[CMD] {pretty}")

            proc = subprocess.Popen(cmd)
            proc.wait(timeout=15)
            self.procs[(remote_host, remote_port)] = proc

    def add_tunnel(self, model: str, host: str, port: int) -> bool:
        """Dynamically add a new tunnel.

        Returns True if tunnel was added, False if it already exists.
        """
        key = (host, port)
        if key in self.procs:
            return False

        cmd = self._build_cmd(host, port)
        kill_tunnel(port)
        print(f"[SSH] Opening tunnel: localhost:{port} -> {host}:{port}")
        proc = subprocess.Popen(cmd)
        proc.wait(timeout=15)
        self.procs[key] = proc
        self.endpoints.append((model, host, port))
        return True

    def remove_tunnel(self, host: str, port: int) -> bool:
        """Remove a tunnel.

        Returns True if tunnel was removed, False if it didn't exist.
        """
        key = (host, port)
        if key not in self.procs:
            return False

        proc = self.procs.pop(key)
        print(f"[SSH] Closing tunnel {key} (pid={proc.pid})")
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()

        # Remove from endpoints list
        self.endpoints = [e for e in self.endpoints if (e[1], e[2]) != key]
        return True

    def stop(self):
        """Stop all tunnels."""
        for key, proc in list(self.procs.items()):
            print(f"[SSH] Closing tunnel {key} (pid={proc.pid})")
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
            self.procs.pop(key, None)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()
