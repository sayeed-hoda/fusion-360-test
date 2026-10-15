"""Outbound agent_ws_v1 client. The WebSocket loop is async. Add-in calls stay sync."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from urllib.parse import urlparse

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from fusion_agent.core.errors import AgentError
from fusion_agent.transport.backoff import (
    COMMAND_TIMEOUT_S,
    MAX_INBOUND_MESSAGE_BYTES,
    QUIET_AFTER_ATTEMPTS,
    backoff_seconds,
)
from fusion_agent.transport.device_id import get_or_create
from fusion_agent.transport.pairing import (
    HELLO_REJECTED_USER_MESSAGE,
    INSECURE_GATEWAY_USER_MESSAGE,
    UNAUTHORIZED_USER_MESSAGE,
    UNTRUSTED_GATEWAY_USER_MESSAGE,
    is_hello_rejected,
    is_insecure_gateway,
    is_loopback_host,
    is_trusted_gateway,
    is_unauthorized_failure,
)
from fusion_agent.version import PLUGIN_VERSION
from fusion_agent.wire import Envelope, dumps, omit_none

# How often the daemon asks whether the add-in on port 9876 is up.
# A Tymeline host is dialed only after that check. The local REPL is loopback,
# so that socket opens immediately and the chat can leave its wait.
FUSION_POLL_S = 20.0
_LOG = logging.getLogger("fusion_agent.ws")


class _HelloRejected(Exception):
    pass


class _PermanentAuthFailure(Exception):
    pass


class WsClient:
    """Dials the Tymeline gateway and dispatches commands to ``service``."""

    def __init__(
        self,
        gateway_url: str,
        device_token: str,
        token_id: str = "",
        service: Any = None,
        status: Callable[[str], None] | None = None,
        fusion_poll_s: float = FUSION_POLL_S,
    ) -> None:
        if service is None:
            raise ValueError("WsClient requires a CommandService")
        self._gateway_url = gateway_url
        self._device_token = device_token
        self._token_id = token_id or ""
        self._service = service
        self._status = status
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_async: asyncio.Event | None = None
        self._ws: Any = None
        self._send_lock: asyncio.Lock | None = None
        self._pool = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="fusion-ws-cmd"
        )
        self._failed_attempts = 0
        self._gave_up = False
        self._hello_rejected = False
        self._connected = False
        self._loop_running = False
        self._insecure_warned = False
        self._fusion_poll_s = fusion_poll_s
        self._fusion_up = False
        self._lock = threading.Lock()

    @property
    def gateway_url(self) -> str:
        return self._gateway_url

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def fusion_connected(self) -> bool:
        with self._lock:
            return self._fusion_up

    @property
    def gave_up(self) -> bool:
        with self._lock:
            return self._gave_up

    def start(self) -> None:
        if self._stop.is_set():
            return
        with self._lock:
            if self._loop_running:
                return
            self._loop_running = True
        self._thread = threading.Thread(
            target=self._thread_main, name="fusion-ws", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        stop_async = self._stop_async
        if loop is not None and stop_async is not None and loop.is_running():
            loop.call_soon_threadsafe(stop_async.set)
            ws = self._ws
            if ws is not None:
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self._close_ws(ws))
                )
        if self._thread is not None:
            self._thread.join(timeout=8)
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._loop_running = False
            self._connected = False

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._connect_loop())
        finally:
            with self._lock:
                self._loop_running = False
                self._connected = False

    async def _connect_loop(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_async = asyncio.Event()
        self._send_lock = asyncio.Lock()
        if not is_trusted_gateway(self._gateway_url):
            with self._lock:
                self._gave_up = True
            self._notify(UNTRUSTED_GATEWAY_USER_MESSAGE)
            return
        while not self._stop.is_set():
            if not self._is_local_repl() and not await self._wait_for_fusion():
                break
            attempt = self._failed_attempts + 1
            stop_after = False
            try:
                if attempt <= QUIET_AFTER_ATTEMPTS:
                    self._notify(f"[Agent] Connecting to {self._gateway_url} ...")
                if not self._insecure_warned and is_insecure_gateway(self._gateway_url):
                    self._insecure_warned = True
                    self._notify(INSECURE_GATEWAY_USER_MESSAGE)
                await self._one_connection()
            except asyncio.CancelledError:
                break
            except _PermanentAuthFailure:
                with self._lock:
                    self._gave_up = True
                self._notify(UNAUTHORIZED_USER_MESSAGE)
                stop_after = True
            except _HelloRejected:
                with self._lock:
                    self._hello_rejected = True
                    self._gave_up = True
                self._notify(HELLO_REJECTED_USER_MESSAGE)
                stop_after = True
            except Exception as exc:
                if self._stop.is_set():
                    break
                if is_unauthorized_failure(exc):
                    with self._lock:
                        self._gave_up = True
                    self._notify(UNAUTHORIZED_USER_MESSAGE)
                    stop_after = True
                elif is_hello_rejected(getattr(exc, "reason", None) or str(exc)):
                    with self._lock:
                        self._hello_rejected = True
                        self._gave_up = True
                    self._notify(HELLO_REJECTED_USER_MESSAGE)
                    stop_after = True
                else:
                    with self._lock:
                        self._failed_attempts += 1
                        failed = self._failed_attempts
                    if failed <= QUIET_AFTER_ATTEMPTS:
                        detail = self._public_error(exc)
                        self._notify(f"[Agent] Tymeline connection failed: {detail}")
                    elif failed == QUIET_AFTER_ATTEMPTS + 1:
                        self._notify(
                            "[Agent] Still trying to reach Tymeline; "
                            "will keep retrying quietly."
                        )
            finally:
                with self._lock:
                    self._connected = False
                self._ws = None

            if stop_after or self._stop.is_set():
                break
            delay = backoff_seconds(self._failed_attempts)
            if self._failed_attempts <= QUIET_AFTER_ATTEMPTS:
                self._notify(f"[Agent] Reconnecting in {delay}s ...")
            try:
                assert self._stop_async is not None
                await asyncio.wait_for(self._stop_async.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                continue

    async def _one_connection(self) -> None:
        headers = {"Authorization": f"Bearer {self._device_token}"}
        if self._token_id:
            headers["X-Tymeline-Token-Id"] = self._token_id
        try:
            ws = await connect(
                self._gateway_url,
                additional_headers=headers,
                max_size=MAX_INBOUND_MESSAGE_BYTES,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=10,
                proxy=None,
            )
        except InvalidStatus as exc:
            if is_unauthorized_failure(exc):
                raise _PermanentAuthFailure() from exc
            raise
        self._ws = ws
        with self._lock:
            self._failed_attempts = 0
            self._hello_rejected = False
            self._connected = True
        self._notify(f"[Agent] WebSocket connected to {self._gateway_url}.")
        status = await asyncio.get_running_loop().run_in_executor(
            self._pool, self._addon_status
        )
        if status is None and not self._is_local_repl():
            self._notify(
                "[Agent] Fusion closed before hello. Waiting for the add-in again."
            )
            await self._close_ws(ws)
            return
        if status is None:
            status = {}
        try:
            await self._send(
                {
                    "type": "event",
                    "event": "hello",
                    "payload": self._hello_payload(status),
                }
            )
            with self._lock:
                self._fusion_up = bool(status.get("connected"))
            watch = asyncio.create_task(self._watch_fusion())
            try:
                await self._receive_loop(ws)
            finally:
                watch.cancel()
                with self._lock:
                    self._fusion_up = False
        finally:
            await self._close_ws(ws)
            if (
                not self._stop.is_set()
                and not self._gave_up
                and not self._hello_rejected
            ):
                self._notify(
                    f"[Agent] WebSocket disconnected from {self._gateway_url}."
                )

    async def _receive_loop(self, ws: Any) -> None:
        try:
            async for raw in ws:
                if self._stop.is_set():
                    return
                if not isinstance(raw, str):
                    continue
                try:
                    env = Envelope.from_json(raw)
                except Exception:
                    continue
                asyncio.create_task(self._handle(env))
        except ConnectionClosed as exc:
            reason = ""
            rcvd = getattr(exc, "rcvd", None)
            if rcvd is not None:
                reason = getattr(rcvd, "reason", "") or ""
            elif getattr(exc, "reason", None):
                reason = str(exc.reason)
            if is_hello_rejected(reason):
                raise _HelloRejected() from exc
            return

    async def _handle(self, env: Envelope) -> None:
        kind = (env.type or "").lower()
        if kind == "ping":
            await self._send({"type": "pong"})
            return
        if kind == "event":
            return
        try:
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    self._pool, self._dispatch_sync, env
                ),
                timeout=COMMAND_TIMEOUT_S,
            )
            await self._send(
                omit_none(
                    {
                        "id": env.id,
                        "type": "response",
                        "payload": _to_payload(result),
                    }
                )
            )
        except asyncio.TimeoutError:
            await self._send_error(
                env.id, "fusion_busy", "command timed out after 120s"
            )
        except AgentError as exc:
            await self._send_error(env.id, exc.code, exc.message)
        except Exception as exc:
            _LOG.exception("ws command %s failed", env.type)
            await self._send_error(env.id, "internal_error", self._public_error(exc))

    def _dispatch_sync(self, env: Envelope) -> Any:
        kind = (env.type or "").lower()
        payload = env.payload if isinstance(env.payload, dict) else {}
        if kind == "get_status":
            return self._service.get_status()
        if kind == "list_tools":
            return self._service.list_tools()
        if kind == "call_tool":
            return self._service.call_tool(payload)
        raise AgentError.not_supported(f"Unknown command '{env.type}'.")

    async def _wait_for_fusion(self) -> bool:
        """Block until the add-in answers. Return False when stop was requested."""
        announced = False
        while not self._stop.is_set():
            status = await asyncio.get_running_loop().run_in_executor(
                self._pool, self._addon_status
            )
            if status is not None:
                if announced:
                    self._notify("[Agent] Fusion add-in is connected.")
                return True
            if not announced:
                seconds = (
                    int(self._fusion_poll_s)
                    if self._fusion_poll_s >= 1
                    else self._fusion_poll_s
                )
                self._notify(
                    f"[Agent] Fusion is not open. Checking the add-in every {seconds}s "
                    "before connecting to Tymeline."
                )
                announced = True
            if await self._sleep(self._fusion_poll_s):
                return False
        return False

    async def _watch_fusion(self) -> None:
        """While the gateway socket is up, notice Fusion close and reopen."""
        while not self._stop.is_set() and self._ws is not None:
            if await self._sleep(self._fusion_poll_s):
                return
            status = await asyncio.get_running_loop().run_in_executor(
                self._pool, self._addon_status
            )
            with self._lock:
                was_up = self._fusion_up
            if status is None:
                if was_up:
                    with self._lock:
                        self._fusion_up = False
                    seconds = (
                        int(self._fusion_poll_s)
                        if self._fusion_poll_s >= 1
                        else self._fusion_poll_s
                    )
                    self._notify(
                        "[Agent] Fusion add-in disconnected. "
                        f"Checking again every {seconds}s."
                    )
                continue
            if not was_up:
                with self._lock:
                    self._fusion_up = True
                self._notify("[Agent] Fusion add-in is connected. Sending hello.")
                try:
                    await self._send(
                        {
                            "type": "event",
                            "event": "hello",
                            "payload": self._hello_payload(status),
                        }
                    )
                except Exception:
                    return

    async def _sleep(self, seconds: float) -> bool:
        """Return True when stop was requested during the wait."""
        try:
            assert self._stop_async is not None
            await asyncio.wait_for(self._stop_async.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    def _is_local_repl(self) -> bool:
        """True when the key points at this machine. That host is the chat."""
        return is_loopback_host(urlparse(self._gateway_url).hostname or "")

    def _addon_status(self) -> dict[str, Any] | None:
        try:
            status = self._service.get_status()
        except Exception:
            return None
        if not isinstance(status, dict) or not status.get("connected"):
            return None
        return status

    def _hello_payload(self, status: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol_version": "agent_ws_v1",
            "device_id": get_or_create(),
            "plugin_version": PLUGIN_VERSION,
            "cad_kind": "fusion360",
            "supported_schemas": ["fusion_mcp_tools_v1"],
            "confirmation_required": False,
        }
        if status.get("fusion_version"):
            payload["fusion_version"] = status["fusion_version"]
        if status.get("document"):
            payload["document"] = status["document"]
        return payload

    async def _send(self, message: dict[str, Any]) -> None:
        ws = self._ws
        lock = self._send_lock
        if ws is None or lock is None:
            return
        text = dumps(message)
        async with lock:
            if self._ws is None:
                return
            await self._ws.send(text)

    async def _send_error(self, req_id: str | None, code: str, message: str) -> None:
        await self._send(
            omit_none(
                {
                    "id": req_id,
                    "type": "error",
                    "payload": {"error": code, "message": message},
                }
            )
        )

    async def _close_ws(self, ws: Any) -> None:
        try:
            await ws.close()
        except Exception:
            pass

    def _public_error(self, exc: BaseException) -> str:
        return self._redact(str(exc))

    def _redact(self, text: str) -> str:
        if self._device_token and self._device_token in text:
            return text.replace(self._device_token, "[redacted]")
        return text

    def _notify(self, message: str) -> None:
        safe = self._redact(message)
        try:
            if self._status is not None:
                self._status(safe)
            else:
                print(safe, flush=True)
        except Exception:
            pass


def _to_payload(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return obj
