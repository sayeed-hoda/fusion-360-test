"""WebSocket the REPL listens on. ``fusion-agent serve`` dials the key URL.

The pairing secret arrives in the ``Authorization`` header and is not read
or logged. Tool calls go back over this socket as ``list_tools`` and
``call_tool``. Fusion stays on the add-in port that ``serve`` already uses.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, Callable

from websockets.asyncio.server import serve

from fusion_agent.core.errors import AgentError
from fusion_agent.transport.backoff import COMMAND_TIMEOUT_S, MAX_INBOUND_MESSAGE_BYTES
from fusion_agent.transport.pairing import CONNECT_PATH
from fusion_agent.transport.paths import WS_PORT
from fusion_agent.wire import Envelope, dumps, omit_none

_HOST = "127.0.0.1"


def listen_url(port: int) -> str:
    return f"ws://{_HOST}:{port}{CONNECT_PATH}"


class FusionReplLink:
    """Accept one ``agent_ws_v1`` client and forward chat tool calls to it."""

    def __init__(
        self,
        port: int = WS_PORT,
        status: Callable[[str], None] | None = None,
        on_change: Callable[[bool], None] | None = None,
    ) -> None:
        if port < 0 or port > 65535:
            raise ValueError("Fusion REPL WebSocket port is out of range.")
        self.port = port
        self._status = status
        self._on_change = on_change
        self._ready = threading.Event()
        self._hello = threading.Event()
        self._hello_payload: dict[str, Any] = {}
        self._bind_error: BaseException | None = None
        self._stopped = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_async: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._ws: Any = None
        self._pending: dict[str, Future[Any]] = {}
        self._next_id = 0
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return listen_url(self.port)

    def start(self) -> None:
        """Bind ``127.0.0.1``. Raises ``OSError`` when the port is taken."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._thread_main, name="fusion-repl-ws", daemon=True
        )
        self._thread.start()
        self._ready.wait()
        if self._bind_error is not None:
            raise self._bind_error

    def wait_for_hello(self, timeout: float | None = None) -> dict[str, Any]:
        """Block until serve sends hello. ``timeout`` is for tests."""
        if not self._hello.wait(timeout):
            raise TimeoutError("Fusion WebSocket did not connect.")
        return dict(self._hello_payload)

    def connection_info(self) -> dict[str, Any]:
        """Listener and peer state. This dict does not include the pairing secret."""
        with self._lock:
            connected = self._ws is not None
            hello = dict(self._hello_payload) if self._hello.is_set() else None
        return {
            "listening": (
                self._thread is not None
                and not self._stopped
                and self._bind_error is None
            ),
            "url": self.url,
            "websocket_connected": connected,
            "hello": hello,
        }

    def list_tools(self) -> Any:
        return self.request("list_tools")

    def call_tool(self, payload: dict[str, Any]) -> Any:
        return self.request("call_tool", payload)

    def request(
        self, kind: str, payload: Any = None, timeout: float = COMMAND_TIMEOUT_S
    ) -> Any:
        """Send one command and wait for the matching response."""
        with self._lock:
            loop = self._loop
            connected = self._ws is not None
            self._next_id += 1
            req_id = str(self._next_id)
        if loop is None or not connected or self._stopped:
            raise AgentError.fusion_busy("Fusion WebSocket is not connected.")
        fut: Future[Any] = Future()

        def _schedule(
            req_id: str = req_id,
            kind: str = kind,
            payload: Any = payload,
            fut: Future[Any] = fut,
        ) -> None:
            asyncio.create_task(self._send_request(req_id, kind, payload, fut))

        try:
            loop.call_soon_threadsafe(_schedule)
        except RuntimeError as exc:
            raise AgentError.fusion_busy("Fusion WebSocket is not connected.") from exc
        try:
            return fut.result(timeout)
        except TimeoutError as exc:
            with self._lock:
                self._pending.pop(req_id, None)
            raise AgentError.fusion_busy(
                f"command timed out after {int(timeout)}s"
            ) from exc

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        loop = self._loop
        stop_async = self._stop_async
        if loop is not None and stop_async is not None and loop.is_running():
            loop.call_soon_threadsafe(stop_async.set)
            ws = self._ws
            if ws is not None:
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self._close_ws(ws))
                )
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self._fail_pending("Fusion WebSocket disconnected.")
        self._peer_changed(False)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        finally:
            self._ready.set()

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_async = asyncio.Event()
        try:
            server = await serve(
                self._handler,
                _HOST,
                self.port,
                process_request=self._process_request,
                max_size=MAX_INBOUND_MESSAGE_BYTES,
                ping_interval=20,
                ping_timeout=20,
            )
        except OSError as exc:
            self._bind_error = exc
            self._ready.set()
            return
        sockets = getattr(server, "sockets", None) or []
        if sockets:
            self.port = int(sockets[0].getsockname()[1])
        self._ready.set()
        await self._stop_async.wait()
        server.close()
        await server.wait_closed()

    def _process_request(self, connection: Any, request: Any) -> Any:
        path = str(getattr(request, "path", "") or "").split("?", 1)[0]
        if len(path) > 1:
            path = path.rstrip("/")
        if path != CONNECT_PATH:
            return connection.respond(404, "Not Found")
        return None

    async def _handler(self, ws: Any) -> None:
        accepted = False
        with self._lock:
            self._ws = ws
        try:
            async for raw in ws:
                if not isinstance(raw, str):
                    continue
                try:
                    env = Envelope.from_json(raw)
                except Exception:
                    continue
                if (env.type or "").lower() == "ping":
                    await ws.send(dumps({"type": "pong"}))
                    continue
                if not accepted:
                    if not self._take_hello(env):
                        self._notify(
                            "Fusion connected, but hello was rejected. "
                            "The socket must send protocol_version and device_id."
                        )
                        await ws.close(code=1008, reason="hello required")
                        return
                    accepted = True
                    continue
                if (env.type or "").lower() == "event":
                    continue
                self._finish(env)
        finally:
            with self._lock:
                current = self._ws is ws
                if current:
                    self._ws = None
            if current:
                if accepted:
                    self._notify("Fusion WebSocket disconnected.")
                    self._peer_changed(False)
                self._fail_pending("Fusion WebSocket disconnected.")

    def _take_hello(self, env: Envelope) -> bool:
        if (env.type or "").lower() != "event" or (env.event or "").lower() != "hello":
            return False
        payload = env.payload if isinstance(env.payload, dict) else {}
        protocol = payload.get("protocol_version")
        device = payload.get("device_id")
        if not isinstance(protocol, str) or not protocol:
            return False
        if not isinstance(device, str) or not device:
            return False
        with self._lock:
            self._hello_payload = dict(payload)
        self._hello.set()
        self._peer_changed(True)
        return True

    async def _send_request(
        self, req_id: str, kind: str, payload: Any, fut: Future[Any]
    ) -> None:
        with self._lock:
            self._pending[req_id] = fut
            ws = self._ws
        if ws is None:
            with self._lock:
                self._pending.pop(req_id, None)
            if not fut.done():
                fut.set_exception(
                    AgentError.fusion_busy("Fusion WebSocket is not connected.")
                )
            return
        message = omit_none({"id": req_id, "type": kind, "payload": payload})
        try:
            await ws.send(dumps(message))
        except Exception as exc:
            with self._lock:
                self._pending.pop(req_id, None)
            if not fut.done():
                fut.set_exception(AgentError.fusion_busy(str(exc)))

    def _finish(self, env: Envelope) -> None:
        with self._lock:
            fut = self._pending.pop(env.id, None) if env.id else None
        if fut is None or fut.done():
            return
        if (env.type or "").lower() == "error":
            body = env.payload if isinstance(env.payload, dict) else {}
            code = body.get("error")
            message = body.get("message")
            if not isinstance(code, str) or not code:
                code = "internal_error"
            if not isinstance(message, str) or not message:
                message = code
            fut.set_exception(AgentError(code, message))
            return
        fut.set_result(env.payload)

    async def _close_ws(self, ws: Any) -> None:
        try:
            await ws.close()
        except Exception:
            pass

    def _fail_pending(self, message: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        error = AgentError.fusion_busy(message)
        for fut in pending:
            if not fut.done():
                fut.set_exception(error)

    def _peer_changed(self, connected: bool) -> None:
        callback = self._on_change
        if callback is None:
            return
        try:
            callback(connected)
        except Exception:
            pass

    def _notify(self, message: str) -> None:
        if self._status is None:
            return
        try:
            self._status(message)
        except Exception:
            pass
