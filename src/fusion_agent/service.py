"""One Fusion command at a time. ``list_tools`` does not open a socket."""

from __future__ import annotations

import socket
from typing import Any

from fusion360_mcp.connection import Fusion360Connection, FusionError
from fusion_agent.catalog import PUBLISHED_NAMES, catalog
from fusion_agent.core.errors import AgentError
from fusion_agent.version import PLUGIN_VERSION

ADDON_HOST = "127.0.0.1"
ADDON_PORT = 9876
STATUS_SCHEMA = "fusion_status_v1"
SUPPORTED_SCHEMAS = ["fusion_mcp_tools_v1"]


class CommandService:
    """Answers ``list_tools``, ``call_tool``, and ``get_status`` for one add-in."""

    def __init__(
        self,
        connection: Any = None,
        host: str = ADDON_HOST,
    ) -> None:
        if host != ADDON_HOST:
            raise ValueError("Fusion add-in host must be 127.0.0.1")
        self.host = host
        self._connection = connection

    def list_tools(self) -> dict[str, Any]:
        """Return the in-process catalog. Does not touch Fusion."""
        return catalog()

    def get_status(self) -> dict[str, Any]:
        """Ping the add-in. ``connected`` is false when Fusion is closed."""
        document: str | None = None
        fusion_version: str | None = None
        connected = False
        try:
            conn = self._require_connection()
            if conn.ping():
                connected = True
                scene = conn.send_command("get_scene_info", {})
                if isinstance(scene, dict):
                    document = _nonempty_str(scene.get("design_name"))
                    fusion_version = _nonempty_str(scene.get("fusion_version"))
        except Exception:
            connected = False
            document = None
            fusion_version = None
        payload: dict[str, Any] = {
            "schema": STATUS_SCHEMA,
            "cad_kind": "fusion360",
            "plugin_version": PLUGIN_VERSION,
            "connected": connected,
            "supported_schemas": list(SUPPORTED_SCHEMAS),
        }
        if document is not None:
            payload["document"] = document
        if fusion_version is not None:
            payload["fusion_version"] = fusion_version
        return payload

    def call_tool(self, payload: Any) -> dict[str, Any]:
        """Send one allowed command. Hidden and unknown names are not sent."""
        if not isinstance(payload, dict):
            raise AgentError.bad_request("call_tool payload must be an object")
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            raise AgentError.bad_request("missing name")
        if "arguments" not in payload or payload.get("arguments") is None:
            arguments: Any = {}
        else:
            arguments = payload["arguments"]
        if not isinstance(arguments, dict):
            raise AgentError.bad_request("arguments must be an object")
        if name not in PUBLISHED_NAMES:
            raise AgentError.not_supported(f"Unknown command '{name}'.")
        try:
            result = self._require_connection().send_command(name, arguments)
        except FusionError as exc:
            if exc.error_kind == "TIMEOUT":
                raise AgentError.fusion_busy(str(exc)) from exc
            raise AgentError.internal_error(str(exc)) from exc
        except (TimeoutError, socket.timeout, ConnectionError) as exc:
            raise AgentError.fusion_busy(str(exc)) from exc
        return {"success": True, "name": name, "result": result}

    def _require_connection(self) -> Any:
        if self._connection is None:
            self._connection = Fusion360Connection(self.host, ADDON_PORT)
        return self._connection


def _nonempty_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
