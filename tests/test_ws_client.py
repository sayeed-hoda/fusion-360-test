"""The gateway socket stays closed until the add-in answers. No socket to port 9876."""

from __future__ import annotations

import asyncio
import json

import pytest

from fusion_agent.transport.ws_client import WsClient


class _Status:
    def __init__(self, connected: bool, document: str | None = None):
        self.connected = connected
        self.document = document
        self.calls = 0

    def get_status(self) -> dict:
        self.calls += 1
        payload = {
            "schema": "fusion_status_v1",
            "cad_kind": "fusion360",
            "connected": self.connected,
            "supported_schemas": ["fusion_mcp_tools_v1"],
        }
        if self.document:
            payload["document"] = self.document
        return payload


def test_websocket_stays_closed_while_fusion_is_down(monkeypatch: pytest.MonkeyPatch):
    def forbid_connect(*_args, **_kwargs):
        raise AssertionError("WebSocket opened while Fusion was down")

    monkeypatch.setattr("fusion_agent.transport.ws_client.connect", forbid_connect)
    lines: list[str] = []
    service = _Status(connected=False)
    client = WsClient(
        "ws://127.0.0.1:9/api/v1/integrations/plugin/connect",
        "super-secret-value",
        service=service,
        status=lines.append,
        fusion_poll_s=0.05,
    )
    client.start()
    try:
        import time

        for _ in range(40):
            if service.calls >= 1 and any("not open" in line for line in lines):
                break
            time.sleep(0.05)
        assert service.calls >= 1
        assert any("not open" in line for line in lines)
        assert all("super-secret-value" not in line for line in lines)
    finally:
        client.stop()


def test_websocket_opens_after_fusion_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    sent: list[str] = []

    class FakeWS:
        def __init__(self) -> None:
            self._closed = asyncio.Event()

        def __aiter__(self):
            return self

        async def __anext__(self):
            await self._closed.wait()
            raise StopAsyncIteration

        async def send(self, text: str) -> None:
            sent.append(text)

        async def close(self) -> None:
            self._closed.set()

    async def fake_connect(*_args, **_kwargs):
        return FakeWS()

    monkeypatch.setattr("fusion_agent.transport.ws_client.connect", fake_connect)
    monkeypatch.setattr(
        "fusion_agent.transport.device_id.device_id_path",
        lambda: tmp_path / "device_id",
    )
    from fusion_agent.transport import device_id

    device_id.reset_cache()
    service = _Status(connected=True, document="Untitled")
    client = WsClient(
        "ws://127.0.0.1:9/api/v1/integrations/plugin/connect",
        "super-secret-value",
        service=service,
        status=lambda _line: None,
        fusion_poll_s=0.05,
    )
    client.start()
    try:
        import time

        for _ in range(40):
            if sent:
                break
            time.sleep(0.05)
        assert sent, "hello was not sent after Fusion answered"
        hello = json.loads(sent[0])
        assert hello["type"] == "event"
        assert hello["event"] == "hello"
        payload = hello["payload"]
        assert payload["cad_kind"] == "fusion360"
        assert payload["supported_schemas"] == ["fusion_mcp_tools_v1"]
        assert payload["document"] == "Untitled"
        assert payload["protocol_version"] == "agent_ws_v1"
        assert "fusion_version" not in payload
        assert "super-secret-value" not in sent[0]
    finally:
        client.stop()
        device_id.reset_cache()
