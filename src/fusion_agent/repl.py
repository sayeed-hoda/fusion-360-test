"""Chat with a GLM model after Fusion opens the pairing WebSocket.

The REPL listens on the loopback ``agent_ws_v1`` gateway. Pairing in Fusion
starts the daemon, which initiates the WebSocket just as it does for cloud.

The GLM key is read from the environment. It is not stored in this repo.

    export GLM_API_KEY='...'
    export GLM_BASE_URL='https://example.modal.direct/v1'   # optional
    uv run fusion-agent repl
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from fusion_agent.core.errors import AgentError

DEFAULT_BASE_URL = (
    "https://hodamohdsayeedul--ep-glm-5-3-flash-server.us-west.modal.direct/v1"
)
DEFAULT_MODEL = "glm-5.3-flash"
MAX_TOOL_ROUNDS = 16
GLM_READ_TIMEOUT_S = 300
GLM_READ_ATTEMPTS = 3
GLM_READ_RETRY_WAIT_S = 2
FUSION_POLL_S = 2.0
MISSING_KEY_MESSAGE = (
    "Set GLM_API_KEY in the environment. Do not put the key in a file in this repo."
)
WAITING_MESSAGE = (
    "Waiting for the Fusion add-in on 127.0.0.1:9876. "
    "Checking every {seconds}s. Start TymelineFusion360 (Shift+S > Add-Ins > Run)."
)
WAITING_SOCKET_MESSAGE = (
    "WebSocket listening at {url}. Waiting for Fusion to connect; "
    "paste the local TYM1 key and click Pair in Fusion."
)
REPL_HELP = """\
/status  WebSocket peer, then Fusion TCP through that socket
/ws      listener URL and whether a peer is connected
/tcp     ping Fusion on 127.0.0.1:9876 through the WebSocket
/help    list these commands
/quit    leave the chat"""
_HELLO_FIELDS = (
    "protocol_version",
    "device_id",
    "cad_kind",
    "document",
    "plugin_version",
    "fusion_version",
)
_TCP_FIELDS = (
    "document",
    "fusion_version",
    "plugin_version",
    "schema",
    "cad_kind",
)
SYSTEM = """You control Fusion 360 through the tools this process exposes.
Call list_tools once. It returns each tool's name, description, and parameters.
Then call call_tool with that name and an arguments object from the schema.
Do not invent tool names. Lengths are centimetres. Fusion's API uses centimetres.
Name every body and sketch you create, and reuse those names in later calls.
One call_tool is one Fusion command. Wait for its result before the next edit.
A render_view result includes a screenshot. Look at it before more edits.
Say what you changed in one or two sentences after the tools finish."""


def api_key_from_env() -> str:
    """Return ``GLM_API_KEY`` or ``OPENAI_API_KEY``. Empty when neither is set."""
    for name in ("GLM_API_KEY", "OPENAI_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def tool_schemas() -> list[dict[str, Any]]:
    def fn(
        name: str,
        description: str,
        properties: dict[str, Any],
        required: list[str],
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }

    return [
        fn(
            "list_tools",
            "List the Fusion MCP tools you may call, including each parameter schema.",
            {},
            [],
        ),
        fn(
            "call_tool",
            "Call one tool returned by list_tools.",
            {
                "name": {
                    "type": "string",
                    "description": "Tool name from list_tools.",
                },
                "arguments": {
                    "type": "object",
                    "description": "Arguments matching that tool's parameters.",
                    "additionalProperties": True,
                },
            },
            ["name"],
        ),
    ]


def _tool_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    raw = arguments.get("arguments") or {}
    if isinstance(raw, str):
        raw = json.loads(raw) if raw else {}
    if not isinstance(raw, dict):
        raise ValueError("arguments must be an object")
    return raw


def run_tool(name: str, arguments: dict[str, Any], service: Any) -> Any:
    """Run one model tool. list_tools reads the catalog. call_tool sends one command."""
    if name == "list_tools":
        return service.list_tools()
    if name != "call_tool":
        return {"success": False, "error": f"unknown tool: {name}"}
    tool_name = arguments.get("name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return {"success": False, "error": "bad_request", "message": "missing name"}
    try:
        payload: dict[str, Any] = {"name": tool_name.strip()}
        if "arguments" in arguments and arguments.get("arguments") is not None:
            payload["arguments"] = _tool_arguments(arguments)
        return service.call_tool(payload)
    except AgentError as exc:
        return {"success": False, "error": exc.code, "message": exc.message}
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"success": False, "error": "bad_request", "message": str(exc)}


def tool_message_content(result: Any) -> str | list[dict[str, Any]]:
    """Text for a normal result. A render PNG is an image part the model can see."""
    text_result = result
    image: dict[str, str] | None = None
    if isinstance(result, dict):
        nested = result.get("result")
        inner = nested if isinstance(nested, dict) else result
        encoded = inner.get("image_base64") if isinstance(inner, dict) else None
        if isinstance(encoded, str) and encoded:
            image_format = inner.get("image_format") or "png"
            image = {"mime": f"image/{image_format}", "data": encoded}
            cleaned = {
                key: value for key, value in inner.items() if key != "image_base64"
            }
            if inner is result:
                text_result = cleaned
            else:
                text_result = {key: value for key, value in result.items()}
                text_result["result"] = cleaned
    text = json.dumps(text_result, default=str)
    if image is None:
        return text
    return [
        {"type": "text", "text": text},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{image['mime']};base64,{image['data']}"},
        },
    ]


def parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("tool arguments must be a JSON object")
    return parsed


def assistant_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip()
    return ""


def chat_once(
    messages: list[dict[str, Any]],
    service: Any,
    complete: Callable[[list[dict[str, Any]]], dict[str, Any]],
    log: Callable[[str], None] = print,
    run: Callable[[str, dict[str, Any], Any], Any] | None = None,
) -> str:
    """Send messages, run tool calls locally, and return the model's final text."""
    invoke = run if run is not None else run_tool
    for _ in range(MAX_TOOL_ROUNDS):
        message = complete(messages)
        messages.append(message)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return assistant_text(message)
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name") or ""
            try:
                arguments = parse_arguments(function.get("arguments"))
                log(f"tool {name} {json.dumps(arguments)}")
                result = invoke(name, arguments, service)
            except Exception as exc:
                result = {"success": False, "error": str(exc)}
                log(f"tool {name} failed: {exc}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or name,
                    "content": tool_message_content(result),
                }
            )
    return "Stopped: the model kept calling tools without a final answer."


def _read_timed_out(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    reason = getattr(exc, "reason", None)
    return isinstance(reason, TimeoutError)


def glm_read_timeout_s() -> float:
    """Seconds to wait for one model reply. GLM_READ_TIMEOUT_S overrides the default."""
    raw = os.environ.get("GLM_READ_TIMEOUT_S")
    if not raw:
        return float(GLM_READ_TIMEOUT_S)
    try:
        value = float(raw)
    except ValueError:
        return float(GLM_READ_TIMEOUT_S)
    if value <= 0:
        return float(GLM_READ_TIMEOUT_S)
    return value


def redact(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "[redacted]")
    return text


def post_chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    attempts: int = GLM_READ_ATTEMPTS,
    wait_s: float = GLM_READ_RETRY_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "tools": tool_schemas(),
            "temperature": 0.2,
        }
    ).encode()
    payload: dict[str, Any] | None = None
    timeout_s = glm_read_timeout_s()
    for attempt in range(1, attempts + 1):
        log(f"Asking GLM ({attempt}/{attempts}, up to {timeout_s:.0f}s).")
        started = time.monotonic()
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                payload = json.loads(response.read().decode())
            log(f"GLM replied in {time.monotonic() - started:.0f}s.")
            break
        except urllib.error.HTTPError as exc:
            detail = redact(exc.read().decode(errors="replace")[:500], api_key)
            raise RuntimeError(f"GLM request failed: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            if not _read_timed_out(exc) or attempt == attempts:
                if _read_timed_out(exc):
                    raise RuntimeError(
                        f"GLM read timed out after {attempts} attempts."
                    ) from exc
                raise
            log(f"GLM read timed out. Retrying ({attempt + 1}/{attempts}).")
            sleep(wait_s)
        except TimeoutError as exc:
            if attempt == attempts:
                raise RuntimeError(
                    f"GLM read timed out after {attempts} attempts."
                ) from exc
            log(f"GLM read timed out. Retrying ({attempt + 1}/{attempts}).")
            sleep(wait_s)
    if payload is None:
        raise RuntimeError(f"GLM read timed out after {attempts} attempts.")
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(
            f"GLM response had no choices: {redact(json.dumps(payload)[:500], api_key)}"
        )
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("GLM response missing message")
    return message


def resolve_model(base_url: str, api_key: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    url = base_url.rstrip("/") + "/models"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
        data = payload.get("data") or []
        if data and isinstance(data[0], dict) and data[0].get("id"):
            return str(data[0]["id"])
    except Exception:
        pass
    return DEFAULT_MODEL


def _poll_label(poll_s: float) -> int | float:
    if poll_s >= 1 and float(poll_s).is_integer():
        return int(poll_s)
    return poll_s


def wait_for_addon(
    service: Any,
    write: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = FUSION_POLL_S,
) -> dict[str, Any]:
    """Poll until the add-in answers. Ctrl+C leaves the wait."""
    announced = False
    while True:
        try:
            status = service.get_status()
        except Exception:
            status = None
        if isinstance(status, dict) and status.get("connected"):
            document = status.get("document")
            if isinstance(document, str) and document:
                write(f"Fusion add-in is connected. document={document}")
            else:
                write("Fusion add-in is connected.")
            return status
        if not announced:
            write(WAITING_MESSAGE.format(seconds=_poll_label(poll_s)))
            announced = True
        sleep(poll_s)


def repl_command(line: str, service: Any) -> str:
    """Answer a ``/status``, ``/ws``, or ``/tcp`` check. Other text is not a command."""
    command = line.split(maxsplit=1)[0].lower()
    if command in ("/help", "/?"):
        return REPL_HELP
    if command == "/ws":
        return _format_websocket(service)
    if command == "/tcp":
        return _format_tcp(service)
    if command == "/status":
        return f"{_format_websocket(service)}\n{_format_tcp(service)}"
    return f"Unknown command '{command}'. Type /help."


def _format_websocket(service: Any) -> str:
    snapshot = _websocket_snapshot(service)
    if snapshot is None:
        return "WebSocket: not used. This session talks to Fusion over TCP."
    url = snapshot.get("url") or "(unknown)"
    listening = "listening" if snapshot.get("listening") else "not listening"
    peer = "connected" if snapshot.get("websocket_connected") else "not connected"
    lines = [f"WebSocket: {peer}", f"Listener: {listening} {url}"]
    hello = snapshot.get("hello")
    if isinstance(hello, dict) and hello:
        detail = _detail(hello, _HELLO_FIELDS)
        if detail:
            lines.append(f"Hello: {detail}")
    elif snapshot.get("websocket_connected"):
        lines.append("Hello: not received")
    return "\n".join(lines)


def _format_tcp(service: Any) -> str:
    snapshot = _websocket_snapshot(service)
    via_socket = snapshot is not None
    route = "via WebSocket" if via_socket else "direct"
    if via_socket and not snapshot.get("websocket_connected"):
        return (
            "Fusion TCP 127.0.0.1:9876 via WebSocket: not tested. "
            "No WebSocket peer is connected."
        )
    try:
        status = _probe_fusion(service)
    except AgentError as exc:
        return (
            f"Fusion TCP 127.0.0.1:9876 {route}: failed ({exc.code}: {exc.message})"
        )
    except Exception as exc:
        return f"Fusion TCP 127.0.0.1:9876 {route}: failed ({exc})"
    if not isinstance(status, dict):
        return f"Fusion TCP 127.0.0.1:9876 {route}: unexpected reply {status!r}"
    state = "connected" if status.get("connected") else "not connected"
    line = f"Fusion TCP 127.0.0.1:9876 {route}: {state}"
    detail = _detail(status, _TCP_FIELDS)
    if detail:
        return f"{line}\nTCP detail: {detail}"
    return line


def _websocket_snapshot(service: Any) -> dict[str, Any] | None:
    info = getattr(service, "connection_info", None)
    if not callable(info):
        return None
    snapshot = info()
    if not isinstance(snapshot, dict):
        return None
    return snapshot


def _probe_fusion(service: Any) -> Any:
    """Ping the add-in. A WebSocket session asks its peer to open TCP."""
    direct = getattr(service, "get_status", None)
    if callable(direct):
        return direct()
    request = getattr(service, "request", None)
    if callable(request):
        return request("get_status")
    raise AgentError.not_supported("This session cannot probe Fusion.")


def _detail(payload: dict[str, Any], fields: tuple[str, ...]) -> str:
    parts: list[str] = []
    for name in fields:
        value = payload.get(name)
        if value is None or value == "" or isinstance(value, (dict, list)):
            continue
        parts.append(f"{name}={value}")
    return " ".join(parts)


def run_repl(
    *,
    service: Any,
    complete: Callable[[list[dict[str, Any]]], dict[str, Any]],
    read_line: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    """Read one request per line. ``/quit`` leaves. Other lines go to the model."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}]
    while True:
        try:
            line = read_line("you> ")
        except EOFError:
            write("")
            return
        line = line.strip()
        if not line:
            continue
        if line in ("/quit", "/exit"):
            return
        if line.startswith("/"):
            write(repl_command(line, service))
            continue
        messages.append({"role": "user", "content": line})
        try:
            answer = chat_once(messages, service, complete, log=write)
        except Exception as exc:
            write(str(exc))
            messages.pop()
            continue
        write(answer or "(no text)")


def main(
    service: Any = None,
    complete: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None,
    read_line: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = FUSION_POLL_S,
    link: Any = None,
) -> int:
    """Start the chat. Fusion initiates the agent_ws_v1 connection.

    An explicit ``service`` keeps the direct transport available to tests.
    Returns 1 when the GLM key is missing.
    """
    api_key = api_key_from_env()
    if not api_key:
        write(MISSING_KEY_MESSAGE)
        return 1
    fusion_link = None
    owns_listener = service is None and link is None
    if service is None:
        fusion_link = link
        if fusion_link is None:
            from fusion_agent.repl_link import FusionReplLink

            def publish(connected: bool) -> None:
                current = fusion_link
                target = current.url if current is not None else ""
                note_repl_listener(target, connected=connected)

            from fusion_agent.cli import clear_repl_listener, note_repl_listener

            fusion_link = FusionReplLink(status=write, on_change=publish)
            try:
                fusion_link.start()
            except OSError as exc:
                write(f"Could not listen for Fusion: {exc}")
                fusion_link.stop()
                clear_repl_listener()
                return 1
            snapshot = fusion_link.connection_info()
            note_repl_listener(
                str(snapshot.get("url") or fusion_link.url),
                connected=bool(snapshot.get("websocket_connected")),
            )
        write(WAITING_SOCKET_MESSAGE.format(url=fusion_link.url))
        try:
            hello = fusion_link.wait_for_hello()
        except KeyboardInterrupt:
            write("")
            fusion_link.stop()
            if owns_listener:
                from fusion_agent.cli import clear_repl_listener

                clear_repl_listener()
            return 0
        document = hello.get("document") if isinstance(hello, dict) else None
        if isinstance(document, str) and document:
            write(f"Fusion WebSocket connected. document={document}")
        else:
            write("Fusion WebSocket connected.")
        service = fusion_link
    else:
        try:
            wait_for_addon(service, write=write, sleep=sleep, poll_s=poll_s)
        except KeyboardInterrupt:
            write("")
            return 0
    model = os.environ.get("GLM_MODEL") or DEFAULT_MODEL
    if complete is None:
        base_url = os.environ.get("GLM_BASE_URL") or DEFAULT_BASE_URL
        model = resolve_model(base_url, api_key, os.environ.get("GLM_MODEL"))

        def complete(messages: list[dict[str, Any]]) -> dict[str, Any]:
            return post_chat(base_url, api_key, model, messages, log=write)

    write(
        f"fusion agent ready, model {model}. "
        "Commands: /status, /ws, /tcp, /help, /quit."
    )
    try:
        run_repl(service=service, complete=complete, read_line=read_line, write=write)
    except KeyboardInterrupt:
        write("")
    finally:
        if fusion_link is not None:
            fusion_link.stop()
        if owns_listener:
            from fusion_agent.cli import clear_repl_listener

            clear_repl_listener()
    return 0
