"""REPL WebSocket. No socket to port 9876 and no GLM call."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from fusion_agent.core.errors import AgentError
from fusion_agent.repl import MISSING_KEY_MESSAGE, WAITING_SOCKET_MESSAGE, main
from fusion_agent.repl_link import FusionReplLink
from fusion_agent.wire import dumps

_SECRET = "tym1-secret-do-not-log"


def _hello(document: str = "Untitled") -> dict:
    return {
        "type": "event",
        "event": "hello",
        "payload": {
            "protocol_version": "agent_ws_v1",
            "device_id": "dev-1",
            "cad_kind": "fusion360",
            "document": document,
        },
    }


def _client(url: str, script) -> threading.Thread:
    errors: list[BaseException] = []

    def run() -> None:
        async def body() -> None:
            async with connect(
                url,
                additional_headers={"Authorization": f"Bearer {_SECRET}"},
                open_timeout=5,
            ) as ws:
                await script(ws)

        try:
            asyncio.run(body())
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.errors = errors  # type: ignore[attr-defined]
    thread.start()
    return thread


def test_listener_stays_on_loopback():
    link = FusionReplLink(port=0)
    assert link.url.startswith("ws://127.0.0.1:")
    assert link.url.endswith("/api/v1/integrations/plugin/connect")


def test_second_listener_on_the_same_port_fails():
    first = FusionReplLink(port=0)
    first.start()
    try:
        second = FusionReplLink(port=first.port)
        with pytest.raises(OSError):
            second.start()
        second.stop()
    finally:
        first.stop()


def test_hello_then_list_and_call_and_an_error():
    link = FusionReplLink(port=0)
    link.start()
    try:

        async def script(ws) -> None:
            await ws.send(dumps(_hello()))
            listed = json.loads(await asyncio.wait_for(ws.recv(), 5))
            assert listed["type"] == "list_tools"
            assert "payload" not in listed
            await ws.send(
                dumps(
                    {
                        "id": listed["id"],
                        "type": "response",
                        "payload": {"tools": [{"name": "create_sketch"}]},
                    }
                )
            )
            called = json.loads(await asyncio.wait_for(ws.recv(), 5))
            assert called["type"] == "call_tool"
            assert called["payload"] == {
                "name": "create_sketch",
                "arguments": {"plane": "xy"},
            }
            await ws.send(
                dumps(
                    {
                        "id": called["id"],
                        "type": "response",
                        "payload": {
                            "success": True,
                            "name": "create_sketch",
                            "result": {"ok": True},
                        },
                    }
                )
            )
            failed = json.loads(await asyncio.wait_for(ws.recv(), 5))
            await ws.send(
                dumps(
                    {
                        "id": failed["id"],
                        "type": "error",
                        "payload": {"error": "fusion_busy", "message": "timed out"},
                    }
                )
            )

        thread = _client(link.url, script)
        hello = link.wait_for_hello(timeout=5)
        assert hello["document"] == "Untitled"
        assert link.list_tools() == {"tools": [{"name": "create_sketch"}]}
        result = link.call_tool({"name": "create_sketch", "arguments": {"plane": "xy"}})
        assert result["success"] is True
        with pytest.raises(AgentError) as caught:
            link.call_tool({"name": "extrude", "arguments": {}})
        assert caught.value.code == "fusion_busy"
        assert caught.value.message == "timed out"
        thread.join(timeout=5)
        assert thread.errors == []  # type: ignore[attr-defined]
        assert _SECRET not in json.dumps(hello)
    finally:
        link.stop()


def test_bad_hello_is_rejected_and_a_later_hello_connects():
    lines: list[str] = []
    link = FusionReplLink(port=0, status=lines.append)
    link.start()
    try:

        async def bad(ws) -> None:
            await ws.send(dumps({"type": "event", "event": "hello", "payload": {}}))
            with pytest.raises(ConnectionClosed) as caught:
                await asyncio.wait_for(ws.recv(), 5)
            reason = ""
            frame = caught.value.rcvd
            if frame is not None:
                reason = frame.reason or ""
            assert "hello required" in reason

        bad_thread = _client(link.url, bad)
        bad_thread.join(timeout=5)
        assert bad_thread.errors == []  # type: ignore[attr-defined]
        assert not link._hello.is_set()
        assert all(_SECRET not in line for line in lines)

        release = threading.Event()

        async def good(ws) -> None:
            await ws.send(dumps(_hello("Bracket")))
            await asyncio.to_thread(release.wait, 5)

        good_thread = _client(link.url, good)
        hello = link.wait_for_hello(timeout=5)
        assert hello["document"] == "Bracket"
        release.set()
        good_thread.join(timeout=5)
        assert good_thread.errors == []  # type: ignore[attr-defined]
    finally:
        link.stop()


def test_wrong_path_is_not_the_connect_socket():
    link = FusionReplLink(port=0)
    link.start()
    try:

        async def wrong() -> None:
            await connect(f"ws://127.0.0.1:{link.port}/nope", open_timeout=5)

        with pytest.raises(InvalidStatus) as caught:
            asyncio.run(wrong())
        assert caught.value.response.status_code == 404
    finally:
        link.stop()


def test_repl_waits_for_the_socket_then_calls_a_tool(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GLM_API_KEY", "glm-secret-key")

    class Link:
        url = "ws://127.0.0.1:53117/api/v1/integrations/plugin/connect"

        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.stopped = False

        def wait_for_hello(self) -> dict:
            return {"document": "Untitled", "device_id": "dev-1"}

        def list_tools(self) -> dict:
            return {"tools": []}

        def call_tool(self, payload: dict) -> dict:
            self.calls.append(payload)
            return {"success": True, "name": payload["name"], "result": {"ok": True}}

        def stop(self) -> None:
            self.stopped = True

    link = Link()
    replies = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "1",
                    "function": {
                        "name": "call_tool",
                        "arguments": json.dumps(
                            {"name": "create_box", "arguments": {"width": 1}}
                        ),
                    },
                }
            ],
        },
        {"role": "assistant", "content": "Made a box."},
    ]

    def complete(_messages):
        return replies.pop(0)

    lines: list[str] = []
    steps = iter(["make a box", "/quit"])
    code = main(
        link=link,
        complete=complete,
        read_line=lambda _prompt: next(steps),
        write=lines.append,
    )
    joined = "\n".join(lines)
    assert code == 0
    assert link.stopped is True
    assert link.calls == [{"name": "create_box", "arguments": {"width": 1}}]
    waiting = joined.index("WebSocket listening")
    connected = joined.index("document=Untitled")
    ready = joined.index("fusion agent ready")
    assert waiting < connected < ready
    assert link.url in joined
    assert "glm-secret-key" not in joined
    assert WAITING_SOCKET_MESSAGE.split("{")[0] in joined


def test_interrupt_while_waiting_for_the_socket_does_not_ask_glm(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("GLM_API_KEY", "glm-secret-key")

    class Link:
        url = "ws://127.0.0.1:53117/api/v1/integrations/plugin/connect"

        def __init__(self) -> None:
            self.stopped = False

        def wait_for_hello(self) -> dict:
            raise KeyboardInterrupt

        def stop(self) -> None:
            self.stopped = True

    def forbid(_messages):
        raise AssertionError("GLM was called while Fusion was not connected")

    link = Link()
    lines: list[str] = []
    code = main(link=link, complete=forbid, write=lines.append)
    assert code == 0
    assert link.stopped is True
    assert lines[0].startswith("WebSocket listening")
    assert "fusion agent ready" not in "\n".join(lines)
    assert all("glm-secret-key" not in line for line in lines)


def test_missing_glm_key_does_not_listen(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def forbid(*_args, **_kwargs):
        raise AssertionError("the WebSocket was opened before a GLM key was set")

    monkeypatch.setattr("fusion_agent.repl_link.FusionReplLink", forbid)
    lines: list[str] = []
    code = main(write=lines.append)
    assert code == 1
    assert lines == [MISSING_KEY_MESSAGE]
