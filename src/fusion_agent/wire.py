"""Gateway envelopes. None is omitted on write. False is kept."""

from __future__ import annotations

import json as _json
from dataclasses import dataclass
from typing import Any

from fusion_agent.core.errors import AgentError

_ENVELOPE = ("id", "type", "event", "payload")


def loads(text: str) -> dict[str, Any]:
    """Parse a JSON object. Bare null and non-objects are bad_request."""
    try:
        value = _json.loads(text)
    except _json.JSONDecodeError as exc:
        raise AgentError.bad_request(f"malformed JSON: {exc.msg}") from exc
    if value is None:
        raise AgentError.bad_request("JSON deserialized to null")
    if not isinstance(value, dict):
        raise AgentError.bad_request("expected a JSON object")
    return value


def dumps(obj: Any) -> str:
    """Serialize a wire model or dict. Compact, insertion-order keys."""
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()
    return _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def omit_none(data: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None. Keep False, 0, and empty strings."""
    return {key: value for key, value in data.items() if value is not None}


def fold_properties(data: Any, names: tuple[str, ...]) -> dict[str, Any]:
    """Case-insensitive read of known names. Unknown keys are dropped."""
    if not isinstance(data, dict):
        raise AgentError.bad_request("expected a JSON object")
    lookup = {name.lower(): name for name in names}
    out: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        dest = lookup.get(key.lower())
        if dest is None or value is None:
            continue
        out[dest] = value
    return out


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _as_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


@dataclass
class Envelope:
    id: str | None = None
    type: str = ""
    event: str | None = None
    payload: Any = None

    @classmethod
    def from_json(cls, text: str) -> Envelope:
        return cls.from_dict(loads(text))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Envelope:
        raw = fold_properties(data, _ENVELOPE)
        return cls(
            id=_opt_str(raw.get("id")),
            type=_as_str(raw.get("type", "")),
            event=_opt_str(raw.get("event")),
            payload=raw.get("payload"),
        )

    def to_dict(self) -> dict[str, Any]:
        return omit_none(
            {
                "id": self.id,
                "type": self.type,
                "event": self.event,
                "payload": self.payload,
            }
        )
