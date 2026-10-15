"""Phase 1: catalog and CommandService. No socket to port 9876."""

from __future__ import annotations

import socket

import pytest

from fusion360_mcp.connection import FusionError
from fusion360_mcp.tools import TOOLS
from fusion_agent.catalog import HIDDEN, catalog
from fusion_agent.core.errors import AgentError
from fusion_agent.service import CommandService


class FakeConnection:
    def __init__(
        self,
        *,
        ping_ok: bool = True,
        scene: dict | None = None,
        result: dict | None = None,
    ):
        self.ping_ok = ping_ok
        self.scene = {"design_name": "Untitled"} if scene is None else scene
        self.result = {"sketch_name": "Sketch1"} if result is None else result
        self.sends: list[tuple[str, dict]] = []
        self.pings = 0

    def ping(self) -> bool:
        self.pings += 1
        return self.ping_ok

    def send_command(self, command_type: str, params: dict | None = None, **_kwargs):
        self.sends.append((command_type, params or {}))
        if command_type == "get_scene_info":
            return self.scene
        return self.result


class ExplodingConnection:
    def ping(self) -> bool:
        raise AssertionError("list_tools must not ping")

    def send_command(self, *_args, **_kwargs):
        raise AssertionError("list_tools must not send")


def test_catalog_matches_tools_minus_hidden():
    published = catalog()
    assert published["schema"] == "fusion_mcp_tools_v1"
    expected = [tool for tool in TOOLS if tool["name"] not in HIDDEN]
    assert [item["name"] for item in published["tools"]] == [
        tool["name"] for tool in expected
    ]
    names = {item["name"] for item in published["tools"]}
    assert "execute_code" not in names
    assert "delete_all" not in names
    assert "undo" in names
    sketch = next(
        item for item in published["tools"] if item["name"] == "create_sketch"
    )
    raw = next(tool for tool in TOOLS if tool["name"] == "create_sketch")
    assert sketch["description"] == raw["description"]
    assert sketch["parameters"] == raw["inputSchema"]
    assert set(sketch) == {"name", "description", "parameters"}


def test_list_tools_does_not_call_the_connection():
    service = CommandService(connection=ExplodingConnection())
    listed = service.list_tools()
    assert listed["schema"] == "fusion_mcp_tools_v1"
    assert any(item["name"] == "create_sketch" for item in listed["tools"])


def test_call_tool_sends_one_command_and_returns_the_result():
    conn = FakeConnection(result={"sketch_name": "Sketch1"})
    service = CommandService(connection=conn)
    out = service.call_tool({"name": "create_sketch", "arguments": {"plane": "xy"}})
    assert conn.sends == [("create_sketch", {"plane": "xy"})]
    assert out == {
        "success": True,
        "name": "create_sketch",
        "result": {"sketch_name": "Sketch1"},
    }

    conn.sends.clear()
    service.call_tool({"name": "create_sketch"})
    assert conn.sends == [("create_sketch", {})]


def test_rejected_tools_do_not_call_the_connection():
    conn = FakeConnection()
    service = CommandService(connection=conn)
    cases = [
        {"name": "execute_code", "arguments": {"code": "1"}},
        {"name": "delete_all", "arguments": {}},
        {"name": "apply_plan", "arguments": {}},
        {},
        {"arguments": {"plane": "xy"}},
    ]
    for payload in cases:
        with pytest.raises(AgentError):
            service.call_tool(payload)
    assert conn.sends == []
    assert conn.pings == 0

    with pytest.raises(AgentError) as missing:
        service.call_tool({})
    assert missing.value.code == "bad_request"

    with pytest.raises(AgentError) as hidden:
        service.call_tool({"name": "execute_code", "arguments": {}})
    assert hidden.value.code == "not_supported"

    with pytest.raises(AgentError) as bad_args:
        service.call_tool({"name": "create_sketch", "arguments": ["xy"]})
    assert bad_args.value.code == "bad_request"
    assert conn.sends == []


def test_timeout_is_fusion_busy_and_is_not_retried():
    class TimeoutConnection(FakeConnection):
        def send_command(
            self, command_type: str, params: dict | None = None, **_kwargs
        ):
            self.sends.append((command_type, params or {}))
            raise FusionError("timed out", error_kind="TIMEOUT")

    conn = TimeoutConnection()
    service = CommandService(connection=conn)
    with pytest.raises(AgentError) as exc:
        service.call_tool({"name": "create_sketch", "arguments": {"plane": "xy"}})
    assert exc.value.code == "fusion_busy"
    assert conn.sends == [("create_sketch", {"plane": "xy"})]

    class SocketTimeout(FakeConnection):
        def send_command(
            self, command_type: str, params: dict | None = None, **_kwargs
        ):
            self.sends.append((command_type, params or {}))
            raise socket.timeout("timed out")

    conn2 = SocketTimeout()
    service2 = CommandService(connection=conn2)
    with pytest.raises(AgentError) as exc2:
        service2.call_tool({"name": "create_sketch", "arguments": {}})
    assert exc2.value.code == "fusion_busy"
    assert len(conn2.sends) == 1


def test_status_ping_success_includes_document_and_failure_stays_up():
    conn = FakeConnection(ping_ok=True, scene={"design_name": "Untitled"})
    service = CommandService(connection=conn)
    status = service.get_status()
    assert status["connected"] is True
    assert status["document"] == "Untitled"
    assert status["schema"] == "fusion_status_v1"
    assert status["cad_kind"] == "fusion360"
    assert status["supported_schemas"] == ["fusion_mcp_tools_v1"]
    assert conn.sends == [("get_scene_info", {})]

    down = FakeConnection(ping_ok=False)
    offline = CommandService(connection=down).get_status()
    assert offline["connected"] is False
    assert "document" not in offline
    assert down.sends == []

    class SceneUnavailable(FakeConnection):
        def send_command(self, command_type: str, params=None, **_kwargs):
            assert command_type == "get_scene_info"
            raise RuntimeError("no active design")

    available = CommandService(connection=SceneUnavailable()).get_status()
    assert available["connected"] is True
    assert "document" not in available


def test_non_loopback_host_raises():
    with pytest.raises(ValueError):
        CommandService(host="10.1.2.3")
    with pytest.raises(ValueError):
        CommandService(host="localhost")
