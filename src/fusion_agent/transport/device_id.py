"""Stable per-install device id at ~/.config/tymeline-fusion360/device_id."""

from __future__ import annotations

import uuid
from pathlib import Path

from fusion_agent.transport.paths import device_id_path

_cached: str | None = None


def get_or_create(path: Path | None = None) -> str:
    """Return the persisted uuid4, creating the file on first use.

    If the directory cannot be created, still return an id so hello can be sent.
    """
    global _cached
    if path is None and _cached is not None:
        return _cached
    try:
        dest = path if path is not None else device_id_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file():
            existing = dest.read_text(encoding="utf-8").strip()
            if existing:
                if path is None:
                    _cached = existing
                return existing
        ident = str(uuid.uuid4())
        dest.write_text(ident + "\n", encoding="utf-8")
        if path is None:
            _cached = ident
        return ident
    except OSError:
        ident = str(uuid.uuid4())
        if path is None:
            _cached = ident
        return ident


def reset_cache() -> None:
    """Test helper: forget the process-level cache."""
    global _cached
    _cached = None
