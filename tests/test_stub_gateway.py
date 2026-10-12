"""Stub gateway round trip and a 401 that stops retry. No socket to port 9876."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from websockets.exceptions import InvalidStatus

from fusion_agent.catalog import catalog
from fusion_agent.service import CommandService
from fusion_agent.transport.ws_client import WsClient

_SECRET = "tym1-secret-do-not-log"


class _Conn:
    def __init__(self) -> None:
        self.sends: list[tuple[str, dict]] = []

    def ping(self) -> bool:
        return True

    def send_command(self, command_type: str, params: dict | None = None, **_kwargs):
        self.sends.append((command_type, params or {}))
        if command_type == "get_scene_info":
            return {"design_name": "Untitled", "ok": True}
        return {"ok": True, "sketch_name": "Sketch1"}


class _ScriptedSocket:
    """Async iterator the client treats as a WebSocket. Inbound frames are queued."""

    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._inbound: asyncio.Queue[str | None] = asyncio.Queue()
        self._lock = threading.Lock()
        self._sent: list[str] = []

    def push(self, message: dict) -> None:
        text = json.dumps(message)
        self.loop.call_soon_threadsafe(self._inbound.put_nowait, text)

    def sent(self) -> list[str]:
        with self._lock:
            return list(self._sent)

    async def send(self, text: str) -> None:
        with self._lock:
            self._sent.append(text)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._inbound.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self) -> None:
        self._inbound.put_nowait(None)


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def _by_id(messages: list[dict], req_id: str) -> dict:
    for message in messages:
        if message.get("id") == req_id:
            return message
    raise AssertionError(f"missing response {req_id}")


def test_stub_gateway_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path):
    sockets: list[_ScriptedSocket] = []

    async def fake_connect(*_args, **_kwargs):
        ws = _ScriptedSocket()
        sockets.append(ws)
        return ws

    monkeypatch.setattr("fusion_agent.transport.ws_client.connect", fake_connect)
    monkeypatch.setattr(
        "fusion_agent.transport.device_id.device_id_path",
        lambda: tmp_path / "device_id",
    )
    from fusion_agent.transport import device_id

    device_id.reset_cache()
    conn = _Conn()
    lines: list[str] = []
    client = WsClient(
        "ws://127.0.0.1:9/api/v1/integrations/plugin/connect",
        _SECRET,
        service=CommandService(connection=conn),
        status=lines.append,
        fusion_poll_s=30,
    )
    client.start()
    try:
        _wait_until(lambda: bool(sockets) and bool(sockets[0].sent()))
        ws = sockets[0]
        hello = json.loads(ws.sent()[0])
        assert hello["type"] == "event"
        assert hello["event"] == "hello"
        payload = hello["payload"]
        assert payload["cad_kind"] == "fusion360"
        assert payload["supported_schemas"] == ["fusion_mcp_tools_v1"]
        assert payload["document"] == "Untitled"
        assert "fusion_version" not in payload

        ws.push({"id": "lt", "type": "list_tools"})
        ws.push(
            {
                "id": "ct",
                "type": "call_tool",
                "payload": {
                    "name": "create_sketch",
                    "arguments": {"plane": "xy"},
                },
            }
        )
        ws.push({"type": "ping"})
        ws.push({"id": "ex", "type": "execute_code", "payload": {"code": "1"}})
        ws.push({"id": "ap", "type": "apply_plan", "payload": {"ops": []}})
        ws.push(
            {
                "id": "hide",
                "type": "call_tool",
                "payload": {"name": "execute_code", "arguments": {"code": "1"}},
            }
        )
        ws.push(
            {
                "id": "del",
                "type": "call_tool",
                "payload": {"name": "delete_all"},
            }
        )

        wanted = {"lt", "ct", "ex", "ap", "hide", "del"}

        def done() -> bool:
            parsed = [json.loads(raw) for raw in ws.sent()]
            ids = {item.get("id") for item in parsed}
            has_pong = any(item.get("type") == "pong" for item in parsed)
            return wanted <= ids and has_pong

        _wait_until(done)
        parsed = [json.loads(raw) for raw in ws.sent()]

        listed = _by_id(parsed, "lt")
        assert listed["type"] == "response"
        assert listed["payload"] == catalog()
        names = {tool["name"] for tool in listed["payload"]["tools"]}
        assert "execute_code" not in names
        assert "delete_all" not in names

        called = _by_id(parsed, "ct")
        assert called["type"] == "response"
        assert called["payload"]["success"] is True
        assert called["payload"]["name"] == "create_sketch"
        assert called["payload"]["result"]["ok"] is True

        pong = next(item for item in parsed if item.get("type") == "pong")
        assert pong == {"type": "pong"}

        for req_id in ("ex", "ap", "hide", "del"):
            rejected = _by_id(parsed, req_id)
            assert rejected["type"] == "error"
            assert rejected["payload"]["error"] == "not_supported"

        sketches = [args for name, args in conn.sends if name == "create_sketch"]
        assert sketches == [{"plane": "xy"}]
        sent_names = {name for name, _args in conn.sends}
        assert "execute_code" not in sent_names
        assert "delete_all" not in sent_names
        assert "apply_plan" not in sent_names
        assert all(_SECRET not in raw for raw in ws.sent())
        assert all(_SECRET not in line for line in lines)
    finally:
        client.stop()
        device_id.reset_cache()


class _HandshakeResponse:
    status_code = 401


def test_http_401_stops_retry_and_does_not_log_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    attempts = {"n": 0}

    async def fake_connect(*_args, **_kwargs):
        attempts["n"] += 1
        raise InvalidStatus(_HandshakeResponse())  # type: ignore[arg-type]

    monkeypatch.setattr("fusion_agent.transport.ws_client.connect", fake_connect)

    class _Up:
        def get_status(self) -> dict:
            return {
                "schema": "fusion_status_v1",
                "cad_kind": "fusion360",
                "connected": True,
                "document": "Untitled",
                "supported_schemas": ["fusion_mcp_tools_v1"],
            }

    lines: list[str] = []
    client = WsClient(
        "ws://127.0.0.1:9/api/v1/integrations/plugin/connect",
        _SECRET,
        service=_Up(),
        status=lines.append,
        fusion_poll_s=0.05,
    )
    client.start()
    try:
        assert client._thread is not None
        client._thread.join(timeout=2)
        assert not client._thread.is_alive()
        assert client._gave_up is True
        assert attempts["n"] == 1
        assert any("Pairing rejected" in line for line in lines)
        assert all("Reconnecting" not in line for line in lines)
        assert all(_SECRET not in line for line in lines)
    finally:
        client.stop()
