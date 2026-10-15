"""The add-in answers tymeline_status on the socket thread."""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

SERVER_PATH = (
    Path(__file__).resolve().parent.parent / "addon" / "server" / "socket_server.py"
)


def _server_module():
    """Load the socket server without the add-in package logger."""
    package = types.ModuleType("tymeline_status_server")
    package.__path__ = [str(SERVER_PATH.parent)]
    package.get_logger = lambda name=None: logging.getLogger("tymeline_status_test")
    sys.modules["tymeline_status_server"] = package
    spec = importlib.util.spec_from_file_location(
        "tymeline_status_server.socket_server",
        SERVER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "tymeline_status_server"
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Bridge:
    def __init__(self) -> None:
        self.commands: list[dict] = []

    def submit(self, command: dict) -> dict:
        self.commands.append(command)
        return {"status": "error", "message": "queued"}


class _Client:
    def __init__(self) -> None:
        self.sent = b""

    def sendall(self, data: bytes) -> None:
        self.sent += data


def test_tymeline_status_does_not_use_the_fusion_main_thread():
    module = _server_module()
    bridge = _Bridge()
    server = module.Fusion360MCPServer(bridge, host="127.0.0.1", port=9876)
    server._running = True
    server._socket = object()
    client = _Client()

    server._dispatch(client, {"type": "tymeline_status", "params": {}})

    assert bridge.commands == []
    payload = json.loads(client.sent.decode("utf-8").strip())
    assert payload["status"] == "success"
    assert payload["result"]["schema"] == "tymeline_status_v1"
    assert payload["result"]["listening"] is True
    assert payload["result"]["port"] == 9876
    assert payload["result"]["ok"] is True
