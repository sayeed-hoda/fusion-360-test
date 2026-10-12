"""AgentError. ``code`` is the wire ``payload.error`` string."""

from __future__ import annotations

from typing import Any


class AgentError(Exception):
    """Typed plugin error. ``code`` is the wire ``payload.error`` string."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message}

    @classmethod
    def bad_request(cls, message: str) -> AgentError:
        return cls("bad_request", message)

    @classmethod
    def not_supported(cls, message: str) -> AgentError:
        return cls("not_supported", message)

    @classmethod
    def no_document(cls, message: str = "No active document.") -> AgentError:
        return cls("no_document", message)

    @classmethod
    def stale_plan(cls, message: str) -> AgentError:
        return cls("stale_plan", message)

    @classmethod
    def fusion_busy(cls, message: str) -> AgentError:
        return cls("fusion_busy", message)

    @classmethod
    def internal_error(cls, message: str) -> AgentError:
        return cls("internal_error", message)
