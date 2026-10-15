"""fusion-agent: pair with Tymeline and keep the outbound WebSocket up."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

from fusion_agent.transport import paths
from fusion_agent.transport.pairing import (
    INSECURE_GATEWAY_USER_MESSAGE,
    UNTRUSTED_GATEWAY_USER_MESSAGE,
    PairingToken,
    clear_pairing,
    is_insecure_gateway,
    is_trusted_gateway,
    normalize_gateway_url,
    parse_pairing_token,
    resolve_connect_config,
    save_pairing,
)
from fusion_agent.version import PLUGIN_VERSION

NOT_PAIRED_MESSAGE = (
    "[Agent] Not paired. Set FUSION_AGENT_GATEWAY and FUSION_AGENT_TOKEN, "
    "or run fusion-agent pair."
)


def _remember_cli_executable() -> None:
    """Let a copied Fusion add-in find the CLI that ``uv run`` resolved."""
    raw = shutil.which(sys.argv[0]) or sys.argv[0]
    candidate = Path(raw).resolve()
    if not candidate.is_file() or "fusion-agent" not in candidate.name.lower():
        return
    destination = paths.launcher_hint_path()
    try:
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(str(candidate), encoding="utf-8")
        os.replace(temporary, destination)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """Return whether *pid* still refers to a running process.

    ``os.kill(pid, 0)`` is an existence probe. A missing process is
    ``ProcessLookupError`` on Unix. On Windows the same probe raises
    ``OSError: [WinError 87] The parameter is incorrect``.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists, but this user cannot signal it.
        return True
    except OSError:
        return False
    return True


def _read_pid() -> int | None:
    path = paths.pid_path()
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _write_pid() -> None:
    path = paths.pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")


def _clear_pid() -> None:
    path = paths.pid_path()
    if _read_pid() == os.getpid():
        path.unlink(missing_ok=True)


def _write_status(client: object) -> None:
    """Publish non-secret connection state for the Fusion toolbar."""
    connected = bool(getattr(client, "connected", False))
    gave_up = bool(getattr(client, "gave_up", False))
    payload = {
        "state": "error" if gave_up else ("connected" if connected else "connecting"),
        "fusion_connected": bool(getattr(client, "fusion_connected", False)),
        "gateway_url": str(getattr(client, "gateway_url", "")),
        "pid": os.getpid(),
    }
    path = paths.status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def _clear_status() -> None:
    try:
        paths.status_path().unlink(missing_ok=True)
    except OSError:
        pass


def cmd_status() -> int:
    """Print daemon state without exposing its pairing secret."""
    pid = _read_pid()
    if pid is None or not _pid_alive(pid):
        _clear_status()
        print("Tymeline is disconnected.", flush=True)
        return 0
    try:
        payload = json.loads(paths.status_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("Tymeline is starting.", flush=True)
        return 0
    state = payload.get("state")
    url = payload.get("gateway_url")
    destination = f" to {url}" if isinstance(url, str) and url else ""
    if state == "connected":
        fusion = (
            " Fusion is connected."
            if payload.get("fusion_connected")
            else " Fusion is not currently available."
        )
        print(f"Tymeline is connected{destination}.{fusion}", flush=True)
    elif state == "error":
        print(
            f"Tymeline stopped reconnecting{destination}. Pair again to continue.",
            flush=True,
        )
    else:
        print(f"Tymeline is reconnecting{destination}.", flush=True)
    return 0


def open_serve() -> tuple[object | None, int]:
    """Start the WebSocket when pairing is present. Does not wait for Ctrl+C."""
    existing = _read_pid()
    if existing is not None and existing != os.getpid() and _pid_alive(existing):
        print(f"[Agent] Already running (pid {existing}).", flush=True)
        return None, 1
    try:
        cfg = resolve_connect_config()
    except ValueError as exc:
        print(f"[Agent] Not connecting WebSocket: {exc}", file=sys.stderr, flush=True)
        return None, 1
    if cfg is None:
        print(NOT_PAIRED_MESSAGE, flush=True)
        return None, 1
    if not is_trusted_gateway(cfg.url):
        print(UNTRUSTED_GATEWAY_USER_MESSAGE, file=sys.stderr, flush=True)
        return None, 1
    if is_insecure_gateway(cfg.url):
        print(INSECURE_GATEWAY_USER_MESSAGE, file=sys.stderr, flush=True)
    from fusion_agent.service import CommandService
    from fusion_agent.transport.ws_client import WsClient

    service = CommandService()
    # Keep daemon output ASCII: Fusion redirects this stream on Windows, where
    # Python may select cp1252 instead of UTF-8.
    print(f"[Agent] WebSocket enabled ({cfg.source}) -> {cfg.url}", flush=True)
    client = WsClient(cfg.url, cfg.secret, token_id=cfg.token_id, service=service)
    client.start()
    _write_pid()
    return client, 0


def cmd_serve() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    client, code = open_serve()
    if client is None:
        return code
    stop = threading.Event()

    def _handle(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    print(
        f"[Agent] Tymeline Fusion daemon v{PLUGIN_VERSION}. Ctrl+C to stop.", flush=True
    )
    try:
        while not stop.wait(0.5):
            try:
                _write_status(client)
            except OSError:
                pass
    finally:
        stop_fn = getattr(client, "stop", None)
        if callable(stop_fn):
            stop_fn()
        _clear_status()
        _clear_pid()
    return 0


def cmd_pair(token: str) -> int:
    try:
        parsed = parse_pairing_token(token)
        dest = normalize_gateway_url(parsed.url)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not is_trusted_gateway(dest):
        print(UNTRUSTED_GATEWAY_USER_MESSAGE, file=sys.stderr)
        return 2
    if is_insecure_gateway(dest):
        print(INSECURE_GATEWAY_USER_MESSAGE, file=sys.stderr)
    stored = PairingToken(
        url=dest,
        token_id=parsed.token_id,
        secret=parsed.secret,
        display=parsed.display,
    )
    save_pairing(stored)
    print(f"paired {stored.to_safe_string()}")
    return 0


def cmd_unpair() -> int:
    if clear_pairing():
        print("unpaired")
        return 0
    print("not paired")
    return 0


def cmd_stop() -> int:
    """Stop a running serve process. The pairing file stays."""
    pid = _read_pid()
    if pid is None or not _pid_alive(pid):
        path = paths.pid_path()
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
        print("[Agent] Not running.", flush=True)
        _clear_status()
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"[Agent] Could not stop pid {pid}: {exc}", flush=True)
        return 1
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            print(f"[Agent] Stopped (pid {pid}).", flush=True)
            _clear_status()
            return 0
        time.sleep(0.05)
    print(f"[Agent] Still running (pid {pid}).", flush=True)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fusion-agent",
        description=(
            "Tymeline daemon for Fusion 360. Dials the gateway over agent_ws_v1."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="connect to Tymeline once the Fusion add-in answers")
    pair = sub.add_parser("pair", help="store a TYM1 pairing token")
    pair.add_argument("token")
    sub.add_parser("unpair", help="forget the stored pairing token")
    sub.add_parser("stop", help="stop the running connection")
    sub.add_parser("status", help="show the Tymeline and Fusion connection state")
    sub.add_parser(
        "repl",
        help="listen for Fusion over agent_ws_v1, then chat (needs GLM_API_KEY)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return cmd_serve()
    if args.command == "pair":
        return cmd_pair(args.token)
    if args.command == "unpair":
        return cmd_unpair()
    if args.command == "stop":
        return cmd_stop()
    if args.command == "status":
        return cmd_status()
    if args.command == "repl":
        _remember_cli_executable()
        from fusion_agent.repl import main as repl_main

        return repl_main()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
