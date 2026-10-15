"""Pairing and device-id files. The gateway secret stays in this directory.

Windows uses ``%APPDATA%\\tymeline-fusion360``. macOS uses
``~/Library/Application Support/tymeline-fusion360``. Linux uses
``$XDG_CONFIG_HOME/tymeline-fusion360`` or ``~/.config/tymeline-fusion360``.
``FUSION_AGENT_CONFIG_DIR`` replaces that whole folder when it is set.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

CONFIG_DIRNAME = "tymeline-fusion360"
CONFIG_DIR_ENV = "FUSION_AGENT_CONFIG_DIR"
# Add-in TCP listener. The daemon does not bind this port.
ADDON_HOST = "127.0.0.1"
ADDON_PORT = 9876
# Reserved for a later loopback HTTP page. Unused.
HTTP_PORT = 41829
# Default Tymeline WebSocket port when a pairing URL has no port.
WS_PORT = 53117


def config_root() -> Path:
    """Per-user folder for pairing.json, device_id, and agent.pid."""
    override = os.environ.get(CONFIG_DIR_ENV, "").strip()
    if override:
        return Path(override)
    root = _platform_config_parent() / CONFIG_DIRNAME
    _copy_legacy_config(root)
    return root


def _platform_config_parent() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            return Path(appdata)
        return Path.home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"


def _legacy_config_root() -> Path:
    """Folder written before Windows and macOS had their own locations."""
    return Path.home() / ".config" / CONFIG_DIRNAME


def _copy_legacy_config(root: Path) -> None:
    """Copy a key saved under ~/.config into the OS folder, once.

    Linux already uses that path. The test override never reads the home
    directory. agent.pid is a live process file and stays behind.
    """
    if os.name != "nt" and sys.platform != "darwin":
        return
    legacy = _legacy_config_root()
    if root.resolve() == legacy.resolve():
        return
    old_pairing = legacy / "pairing.json"
    new_pairing = root / "pairing.json"
    if new_pairing.is_file() or not old_pairing.is_file():
        return
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(old_pairing, new_pairing)
    old_device = legacy / "device_id"
    new_device = root / "device_id"
    if old_device.is_file() and not new_device.exists():
        shutil.copy2(old_device, new_device)


def pairing_path() -> Path:
    return config_root() / "pairing.json"


def device_id_path() -> Path:
    return config_root() / "device_id"


def pid_path() -> Path:
    return config_root() / "agent.pid"


def status_path() -> Path:
    """Daemon connection state. This file never contains the pairing secret."""
    return config_root() / "status.json"
