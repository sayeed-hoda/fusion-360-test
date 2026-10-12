"""Local GLM chat over CommandService. No socket to port 9876 and no GLM call."""

from __future__ import annotations

import json

import pytest

from fusion_agent.core.errors import AgentError
from fusion_agent.repl import (
    MISSING_KEY_MESSAGE,
    chat_once,
    main,
    post_chat,
    run_tool,
    tool_message_content,
    wait_for_addon,
)
from fusion_agent.service import CommandService


class _Conn:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    def ping(self) -> bool:
        return True

    def send_command(self, name: str, arguments: dict) -> dict:
        self.sent.append((name, arguments))
        if name == "render_view":
            return {
                "ok": True,
                "image_format": "png",
                "image_base64": "abc123",
                "view": "iso",
            }
        return {"ok": True, "sketch_name": "Sketch1"}


def _service() -> tuple[CommandService, _Conn]:
    conn = _Conn()
    return CommandService(connection=conn), conn


def test_list_tools_does_not_call_fusion_and_hides_blocked_names():
    service, conn = _service()
    listed = run_tool("list_tools", {}, service)
    names = {item["name"] for item in listed["tools"]}
    assert "execute_code" not in names
    assert "delete_all" not in names
    assert "create_sketch" in names
    assert conn.sent == []


def test_call_tool_sends_one_command():
    service, conn = _service()
    result = run_tool(
        "call_tool",
        {"name": "create_sketch", "arguments": {"plane": "xy"}},
        service,
    )
    assert conn.sent == [("create_sketch", {"plane": "xy"})]
    assert result["success"] is True
    assert result["name"] == "create_sketch"
    assert result["result"]["sketch_name"] == "Sketch1"


def test_hidden_tools_are_not_sent():
    service, conn = _service()
    for name in ("execute_code", "delete_all"):
        result = run_tool("call_tool", {"name": name, "arguments": {}}, service)
        assert result["success"] is False
        assert result["error"] == "not_supported"
    missing = run_tool("call_tool", {}, service)
    assert missing["error"] == "bad_request"
    assert conn.sent == []


def test_render_view_image_is_a_part_and_not_repeated_in_the_text():
    content = tool_message_content(
        {
            "success": True,
            "name": "render_view",
            "result": {
                "ok": True,
                "view": "iso",
                "image_format": "png",
                "image_base64": "abc123",
            },
        }
    )
    assert isinstance(content, list)
    text = content[0]["text"]
    assert "abc123" not in text
    assert "iso" in text
    assert content[1]["image_url"]["url"] == "data:image/png;base64,abc123"


def test_chat_once_lists_then_calls_one_tool():
    service, conn = _service()
    replies = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "1",
                    "function": {"name": "list_tools", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "2",
                    "function": {
                        "name": "call_tool",
                        "arguments": json.dumps(
                            {"name": "create_sketch", "arguments": {"plane": "xy"}}
                        ),
                    },
                }
            ],
        },
        {"role": "assistant", "content": "Created Sketch1 on the xy plane."},
    ]

    def complete(_messages):
        return replies.pop(0)

    lines: list[str] = []
    answer = chat_once(
        [{"role": "user", "content": "sketch on xy"}],
        service,
        complete,
        log=lines.append,
    )
    assert answer == "Created Sketch1 on the xy plane."
    assert conn.sent == [("create_sketch", {"plane": "xy"})]
    assert any("list_tools" in line for line in lines)
    assert any("create_sketch" in line for line in lines)


def test_agent_error_from_the_service_is_returned_to_the_model():
    class Broken:
        def call_tool(self, _payload):
            raise AgentError.fusion_busy("timed out")

    result = run_tool("call_tool", {"name": "extrude", "arguments": {}}, Broken())
    assert result == {
        "success": False,
        "error": "fusion_busy",
        "message": "timed out",
    }


def test_missing_key_does_not_open_fusion(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    lines: list[str] = []

    def forbid_status():
        raise AssertionError("Fusion was contacted before a key was set")

    blocked = type("S", (), {"get_status": staticmethod(forbid_status)})()
    code = main(service=blocked, write=lines.append)
    assert code == 1
    assert lines == [MISSING_KEY_MESSAGE]


def test_interrupt_while_waiting_does_not_ask_glm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GLM_API_KEY", "glm-secret-key")

    class Down:
        def get_status(self):
            return {"connected": False}

    def forbid_complete(_messages):
        raise AssertionError("GLM was called while Fusion was down")

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    lines: list[str] = []
    code = main(
        service=Down(),
        complete=forbid_complete,
        write=lines.append,
        sleep=interrupt,
        poll_s=0.01,
    )
    assert code == 0
    assert lines[0].startswith("Waiting for the Fusion add-in")
    assert "fusion agent ready" not in "\n".join(lines)
    assert all("glm-secret-key" not in line for line in lines)


def test_repl_waits_until_the_addin_answers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GLM_API_KEY", "glm-secret-key")
    checks = {"n": 0}

    class Later:
        def get_status(self):
            checks["n"] += 1
            if checks["n"] < 3:
                return {"connected": False}
            return {"connected": True, "document": "Untitled"}

    asked: list[str] = []

    def complete(messages):
        asked.append(messages[-1]["content"])
        return {"role": "assistant", "content": "Ready."}

    lines: list[str] = []
    code = main(
        service=Later(),
        complete=complete,
        read_line=lambda _prompt: "make a box" if not asked else "/quit",
        write=lines.append,
        sleep=lambda _seconds: None,
        poll_s=0.01,
    )
    assert code == 0
    assert checks["n"] == 3
    joined = "\n".join(lines)
    waiting = joined.index("Waiting for the Fusion add-in")
    connected = joined.index("document=Untitled")
    ready = joined.index("fusion agent ready")
    assert waiting < connected < ready
    assert asked == ["make a box"]


def test_wait_keeps_polling_when_status_raises():
    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def get_status(self):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("refused")
            return {"connected": True}

    lines: list[str] = []
    status = wait_for_addon(
        Flaky(), write=lines.append, sleep=lambda _s: None, poll_s=2
    )
    assert status["connected"] is True
    assert lines[0].startswith("Waiting for the Fusion add-in")
    assert "every 2s" in lines[0]
    assert lines[-1] == "Fusion add-in is connected."


def test_repl_runs_one_request_and_quits(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GLM_API_KEY", "glm-secret-key")
    seen: list[str] = []

    class Up:
        def get_status(self):
            return {"connected": True, "document": "Untitled"}

    def complete(messages):
        seen.append(messages[-1]["content"])
        return {"role": "assistant", "content": "Ready."}

    lines_in = iter(["make a box", "/quit"])
    lines_out: list[str] = []
    code = main(
        service=Up(),
        complete=complete,
        read_line=lambda _prompt: next(lines_in),
        write=lines_out.append,
    )
    assert code == 0
    assert seen == ["make a box"]
    assert "Ready." in lines_out
    assert all("glm-secret-key" not in line for line in lines_out)


class _Body:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_post_chat_sends_the_key_only_as_a_header(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, str] = {}

    def urlopen(request, timeout=0):
        seen["auth"] = request.get_header("Authorization")
        seen["url"] = request.full_url
        body = json.loads(request.data.decode())
        seen["tool_names"] = ",".join(
            item["function"]["name"] for item in body["tools"]
        )
        return _Body({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    monkeypatch.setattr("fusion_agent.repl.urllib.request.urlopen", urlopen)
    lines: list[str] = []
    message = post_chat(
        "https://example.test/v1",
        "glm-secret-key",
        "glm-5.3-flash",
        [{"role": "user", "content": "hi"}],
        log=lines.append,
    )
    assert message["content"] == "ok"
    assert seen["auth"] == "Bearer glm-secret-key"
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["tool_names"] == "list_tools,call_tool"
    assert all("glm-secret-key" not in line for line in lines)
