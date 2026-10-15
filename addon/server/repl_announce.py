"""Tell a waiting fusion-agent chat that this add-in is up.

The chat listens on 127.0.0.1:9877. This add-in connects and sends one
JSON line. Modeling commands stay on the add-in's own port 9876.
"""

from __future__ import annotations

import json
import os
import socket
import threading

REPL_HOST = "127.0.0.1"
REPL_PORT = 9877
_RETRY_S = 2.0


def repl_port() -> int:
    raw = os.environ.get("FUSION_AGENT_REPL_PORT", str(REPL_PORT))
    try:
        port = int(raw)
    except ValueError:
        return REPL_PORT
    if port <= 0 or port > 65535:
        return REPL_PORT
    return port


def connect_message(command_port: int, document: str | None = None) -> bytes:
    payload: dict[str, object] = {
        "type": "connect",
        "cad_kind": "fusion360",
        "command_port": command_port,
    }
    if document:
        payload["document"] = document
    return (json.dumps(payload) + "\n").encode("utf-8")


def announce_once(
    command_port: int,
    host: str = REPL_HOST,
    port: int | None = None,
    document: str | None = None,
) -> bool:
    """Connect to the chat and send one line. False when nothing is listening."""
    if host != REPL_HOST:
        return False
    dest = repl_port() if port is None else port
    try:
        sock = socket.create_connection((host, dest), timeout=1.0)
    except OSError:
        return False
    try:
        sock.sendall(connect_message(command_port, document))
    finally:
        sock.close()
    return True


def announce_until_heard(
    command_port: int,
    stop: threading.Event,
    host: str = REPL_HOST,
    port: int | None = None,
) -> bool:
    """Retry until the chat accepts the connect line, or ``stop`` is set."""
    while not stop.is_set():
        if announce_once(command_port, host=host, port=port):
            return True
        stop.wait(_RETRY_S)
    return False
