"""Pairing and device-id files. The gateway secret stays in this directory."""

from __future__ import annotations

from pathlib import Path

CONFIG_DIRNAME = "tymeline-fusion360"
# Add-in TCP listener. The daemon does not bind this port.
ADDON_HOST = "127.0.0.1"
ADDON_PORT = 9876
# Reserved for a later loopback HTTP page. Unused.
HTTP_PORT = 41829
# Default Tymeline WebSocket port when a pairing URL has no port.
WS_PORT = 53117


def config_root() -> Path:
    return Path.home() / ".config" / CONFIG_DIRNAME


def pairing_path() -> Path:
    return config_root() / "pairing.json"


def device_id_path() -> Path:
    return config_root() / "device_id"


def pid_path() -> Path:
    return config_root() / "agent.pid"
