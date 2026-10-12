"""Command list the Tymeline agent is allowed to see.

Reads ``TOOLS`` from the MCP server. ``execute_code`` and ``delete_all`` stay
on that list for Claude and are left out of this one.
"""

from __future__ import annotations

from typing import Any

from fusion360_mcp.tools import TOOLS

SCHEMA = "fusion_mcp_tools_v1"
HIDDEN = frozenset({"execute_code", "delete_all"})


def _published_tools() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in TOOLS:
        name = tool["name"]
        if name in HIDDEN:
            continue
        tools.append(
            {
                "name": name,
                "description": tool["description"],
                "parameters": tool["inputSchema"],
            }
        )
    return tools


_TOOLS = _published_tools()
PUBLISHED_NAMES = frozenset(item["name"] for item in _TOOLS)


def catalog() -> dict[str, Any]:
    """Return ``fusion_mcp_tools_v1``. ``parameters`` is each tool's inputSchema."""
    return {"schema": SCHEMA, "tools": [dict(item) for item in _TOOLS]}
