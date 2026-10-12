"""TYM1 pairing tokens, gateway URL rules, and the on-disk pairing store.

A token is base64, not a signature: anyone holding one can rewrite the URL.
``is_trusted_gateway`` is the check that runs before the secret is sent.
The pairing file is mode 0600.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fusion_agent.transport.paths import WS_PORT, pairing_path

CONNECT_PATH = "/api/v1/integrations/plugin/connect"
DEFAULT_GATEWAY_PORT = WS_PORT
TRUSTED_GATEWAY_SUFFIXES = ("tymeline.app",)
PROTOCOL_VERSION = "agent_ws_v1"
TOKEN_PREFIX = "TYM1."

UNAUTHORIZED_USER_MESSAGE = (
    "[Agent] Pairing rejected — the token is invalid, revoked or expired. "
    "Generate a new one in Tymeline and run fusion-agent pair."
)
UNTRUSTED_GATEWAY_USER_MESSAGE = (
    "[Agent] Refusing to pair: that token points at a host outside Tymeline. "
    "Get a fresh token from Tymeline rather than from anyone else."
)
HELLO_REJECTED_USER_MESSAGE = (
    "[Agent] Connected but hello rejected — "
    "send protocol_version and device_id in hello."
)
INSECURE_GATEWAY_USER_MESSAGE = (
    "[Agent] WARNING: this gateway is plain ws://, not wss://. "
    "The device token is sent in cleartext on every connect and "
    "anyone on the network path can read and reuse it. "
    "Use wss:// for anything but local testing."
)


@dataclass(frozen=True)
class PairingToken:
    url: str
    token_id: str
    secret: str
    display: str = ""

    def to_safe_string(self) -> str:
        extra = f" as='{self.display}'" if self.display else ""
        return f"token_id={self.token_id} url={self.url}{extra}"

    def to_dict(self) -> dict[str, str]:
        return {
            "url": self.url,
            "token_id": self.token_id,
            "secret": self.secret,
            "display": self.display,
        }


@dataclass(frozen=True)
class ConnectConfig:
    url: str
    secret: str
    token_id: str = ""
    display: str = ""
    source: str = "env"


def parse_pairing_token(raw: str | None) -> PairingToken:
    trimmed = (raw or "").strip()
    if not trimmed:
        raise ValueError("Pairing token is empty.")
    if not trimmed.startswith(TOKEN_PREFIX):
        raise ValueError(
            "Pairing token must start with TYM1. — copy the whole token from Tymeline."
        )
    payload = trimmed[len(TOKEN_PREFIX) :]
    if not payload:
        raise ValueError("Pairing token is malformed (no payload).")
    try:
        decoded = _decode_base64url(payload)
    except (ValueError, OSError) as exc:
        raise ValueError("Pairing token is malformed (not valid base64url).") from exc
    try:
        data = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Pairing token is malformed (payload is not JSON).") from exc
    if not isinstance(data, dict):
        raise ValueError("Pairing token is malformed (payload is not JSON).")
    url = _read_string(data, "url")
    token_id = _read_string(data, "token_id")
    secret = _read_string(data, "secret")
    display = _read_string(data, "display")
    if not url or not token_id or not secret:
        raise ValueError(
            "Pairing token is incomplete — generate a new one in Tymeline."
        )
    return PairingToken(url=url, token_id=token_id, secret=secret, display=display)


def expand_bare_host(raw: str) -> str:
    s = raw.strip()
    if "://" in s:
        return s
    if _looks_like_host_port(s):
        return "ws://" + s
    return f"ws://{s}:{DEFAULT_GATEWAY_PORT}"


def normalize_gateway_url(raw: str) -> str:
    if raw is None or not str(raw).strip():
        raise ValueError("Gateway URL is empty.")
    s = expand_bare_host(str(raw).strip())
    parsed = urlparse(s)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"ws", "wss"}:
        raise ValueError(
            "Gateway must be a ws:// or wss:// URL (or a host / host:port)."
        )
    if parsed.query or parsed.fragment:
        raise ValueError(
            "Gateway URL must not contain a query string or fragment — "
            "the pairing secret travels in a header, never in the URL."
        )
    path = parsed.path.rstrip("/")
    if not path:
        path = CONNECT_PATH
    return f"{scheme}://{parsed.netloc}{path}"


def is_loopback_host(host: str) -> bool:
    name = unquote(host.strip().strip("[]")).rstrip(".").lower()
    if name in {"localhost", "::1"}:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def is_trusted_gateway(gateway_url: str | None) -> bool:
    if not gateway_url or not str(gateway_url).strip():
        return False
    parsed = urlparse(str(gateway_url).strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"ws", "wss"}:
        return False
    host = parsed.hostname or ""
    if not host:
        return False
    if is_loopback_host(host):
        return True
    if scheme != "wss":
        return False
    name = host.rstrip(".").lower()
    for suffix in TRUSTED_GATEWAY_SUFFIXES:
        if name == suffix:
            return True
        if name.endswith("." + suffix) and len(name) > len(suffix) + 1:
            return True
    return False


def is_insecure_gateway(gateway_url: str | None) -> bool:
    if not gateway_url or not str(gateway_url).strip():
        return False
    parsed = urlparse(str(gateway_url).strip())
    if (parsed.scheme or "").lower() != "ws":
        return False
    host = parsed.hostname or ""
    if not host:
        return False
    return not is_loopback_host(host)


def is_unauthorized_failure(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status == 401:
        return True
    if getattr(exc, "status_code", None) == 401:
        return True
    needles = ("status code '401'", "http/1.1 401", "401 unauthorized", "http 401")
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        msg = str(current).lower()
        if any(needle in msg for needle in needles) or "unauthorized" in msg:
            return True
        current = current.__cause__ or current.__context__
    return False


def is_hello_rejected(close_description: str | None) -> bool:
    return bool(close_description) and "hello required" in close_description.lower()


def save_pairing(token: PairingToken, path: Path | None = None) -> None:
    dest = path if path is not None else pairing_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(token.to_dict(), indent=2) + "\n"
    dest.write_text(payload, encoding="utf-8")
    os.chmod(dest, 0o600)


def load_pairing(path: Path | None = None) -> PairingToken | None:
    dest = path if path is not None else pairing_path()
    try:
        if not dest.is_file():
            return None
        data = json.loads(dest.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        url = _read_string(data, "url")
        token_id = _read_string(data, "token_id")
        secret = _read_string(data, "secret")
        display = _read_string(data, "display")
        if not url or not token_id or not secret:
            return None
        return PairingToken(url=url, token_id=token_id, secret=secret, display=display)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def clear_pairing(path: Path | None = None) -> bool:
    dest = path if path is not None else pairing_path()
    if not dest.is_file():
        return False
    try:
        dest.unlink()
        return True
    except OSError:
        return False


def resolve_connect_config(
    *,
    gateway: str | None = None,
    token: str | None = None,
    token_id: str | None = None,
    pairing_file: Path | None = None,
) -> ConnectConfig | None:
    """Env gateway and token win; otherwise the pairing file is used."""
    env_url = gateway if gateway is not None else os.environ.get("FUSION_AGENT_GATEWAY")
    env_token = token if token is not None else os.environ.get("FUSION_AGENT_TOKEN")
    env_id = (
        token_id
        if token_id is not None
        else os.environ.get("FUSION_AGENT_TOKEN_ID", "")
    )
    if env_url and env_token:
        return ConnectConfig(
            url=normalize_gateway_url(env_url),
            secret=env_token,
            token_id=env_id or "",
            source="env",
        )
    stored = load_pairing(pairing_file)
    if stored is None:
        return None
    return ConnectConfig(
        url=normalize_gateway_url(stored.url),
        secret=stored.secret,
        token_id=stored.token_id,
        display=stored.display,
        source="pairing",
    )


def _read_string(data: dict[str, Any], name: str) -> str:
    value = data.get(name)
    return value if isinstance(value, str) else ""


def _decode_base64url(value: str) -> bytes:
    builder = value.replace("-", "+").replace("_", "/")
    remainder = len(builder) % 4
    if remainder == 1:
        raise ValueError("invalid base64url length")
    if remainder == 2:
        builder += "=="
    elif remainder == 3:
        builder += "="
    return base64.b64decode(builder, validate=True)


def _looks_like_host_port(s: str) -> bool:
    if any(ch in s for ch in "/?#"):
        return False
    if s.startswith("["):
        end = s.find("]")
        if end <= 1:
            return False
        rest = s[end + 1 :]
        if not rest.startswith(":"):
            return False
        return _valid_port(rest[1:])
    colon = s.rfind(":")
    if colon <= 0 or colon == len(s) - 1:
        return False
    return _valid_port(s[colon + 1 :])


def _valid_port(text: str) -> bool:
    try:
        port = int(text)
    except ValueError:
        return False
    return 1 <= port <= 65535
