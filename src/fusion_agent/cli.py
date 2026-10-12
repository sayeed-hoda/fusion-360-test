"""fusion-agent: pair with Tymeline and keep the outbound WebSocket up."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading

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


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
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
    print(f"[Agent] WebSocket enabled ({cfg.source}) → {cfg.url}", flush=True)
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
            pass
    finally:
        stop_fn = getattr(client, "stop", None)
        if callable(stop_fn):
            stop_fn()
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
    sub.add_parser(
        "repl",
        help="chat with GLM against the local Fusion add-in (needs GLM_API_KEY)",
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
    if args.command == "repl":
        from fusion_agent.repl import main as repl_main

        return repl_main()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
