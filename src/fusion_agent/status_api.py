"""Loopback status API shared by the cloud connection and local chat."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from fusion_agent.transport.paths import ADDON_HOST, HTTP_PORT

STATUS_PATH = "/status"


def status_port() -> int:
    raw = os.environ.get("FUSION_AGENT_HTTP_PORT", str(HTTP_PORT)).strip()
    try:
        port = int(raw)
    except ValueError:
        return HTTP_PORT
    return port if 0 <= port <= 65535 else HTTP_PORT


class ConnectionStatusServer:
    """Serve only whether the active WebSocket currently has a peer."""

    def __init__(
        self,
        connected: Callable[[], bool],
        *,
        host: str = ADDON_HOST,
        port: int | None = None,
    ) -> None:
        if host != ADDON_HOST:
            raise ValueError("Status API host must be 127.0.0.1")
        self._connected = connected
        self.host = host
        self.port = status_port() if port is None else port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.split("?", 1)[0] != STATUS_PATH:
                    self.send_error(404)
                    return
                try:
                    connected = bool(owner._connected())
                except Exception:
                    connected = False
                body = json.dumps({"connected": connected}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *args: Any) -> None:
                del args

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="fusion-status-api",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None


def query_status(
    *,
    host: str = ADDON_HOST,
    port: int | None = None,
    timeout: float = 1.0,
) -> bool | None:
    """Return the live value, or ``None`` when no valid status API answers."""
    target_port = status_port() if port is None else port
    url = f"http://{host}:{target_port}{STATUS_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("connected"), bool):
        return None
    return payload["connected"]


def query_connected(
    *,
    host: str = ADDON_HOST,
    port: int | None = None,
    timeout: float = 1.0,
) -> bool:
    """Return false when the API is absent, malformed, or not connected."""
    return query_status(host=host, port=port, timeout=timeout) is True
