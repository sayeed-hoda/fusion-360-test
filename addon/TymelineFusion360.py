"""
Tymeline Fusion 360 Add-in (v2)

Registers a CustomEvent so all Fusion API calls run on the main thread.
A TCP socket server (daemon thread) accepts JSON commands and dispatches
them through an EventBridge.

Host/port override via env vars FUSION_MCP_HOST and FUSION_MCP_PORT.
Set FUSION_MCP_HOST=0.0.0.0 to expose the add-in on all interfaces
for cross-machine setups (MCP server on a different host). Only do
this on a trusted LAN — the socket has no authentication.
"""

import os
import traceback

import adsk.core
import adsk.fusion

# Globals — prevent GC of handler / server references
_app = None
_ui = None
_bridge = None
_server = None
_handler = None
_log = None


def run(context):
    global _app, _ui, _bridge, _server, _handler, _log

    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        # Late imports so the add-in folder is on sys.path
        from .server import LOG_PATH, get_logger
        from .server.command_handler import CommandHandler
        from .server.event_bridge import EventBridge
        from .server.socket_server import Fusion360MCPServer

        _log = get_logger("main")

        host = os.environ.get("FUSION_MCP_HOST", "localhost")
        port = int(os.environ.get("FUSION_MCP_PORT", "9876"))

        _handler = CommandHandler()
        _bridge = EventBridge(_app, _handler)
        _server = Fusion360MCPServer(_bridge, host=host, port=port)
        _server.start()
        _start_tymeline_connection()
        _install_pairing_button()

        _log.info("TymelineFusion360 loaded - server on %s:%s  (log: %s)",
                  host, port, LOG_PATH)
        if host not in ("localhost", "127.0.0.1"):
            _log.warning("Listening on non-loopback host %s — "
                         "ensure this is a trusted LAN (no auth).", host)

    except Exception:
        msg = traceback.format_exc()
        if _ui:
            _ui.messageBox(f"Tymeline Fusion 360 failed to start:\n{msg}")
        print(f"Tymeline Fusion 360 startup error:\n{msg}")


def _start_tymeline_connection() -> None:
    """Start the saved cloud or local WebSocket pairing with the add-in."""
    try:
        from .server.agent_launch import start_connection

        message = start_connection()
        if _log:
            _log.info("%s", message)
    except Exception:
        if _log:
            _log.exception("Tymeline connection did not start with the add-in")
        else:
            traceback.print_exc()


def _install_pairing_button() -> None:
    """Pair / Unpair on the Design toolbar. A failure leaves the socket up."""
    try:
        from .server.pairing_button import install

        install(_ui)
    except Exception:
        if _log:
            _log.exception("Tymeline pair button was not added")
        else:
            traceback.print_exc()


def _stop_tymeline_connection() -> None:
    """Stop the daemon started by Pair while keeping the saved pairing."""
    try:
        from .server.agent_launch import stop_connection

        message = stop_connection()
        if _log:
            _log.info("%s", message)
    except Exception:
        if _log:
            _log.exception("Tymeline connection did not stop with the add-in")
        else:
            traceback.print_exc()


def stop(context):
    global _app, _ui, _bridge, _server, _handler, _log
    try:
        from .server.pairing_button import remove

        remove(_ui)
    except Exception:
        traceback.print_exc()
    _stop_tymeline_connection()
    try:
        if _server:
            _server.stop()
        if _bridge:
            _bridge.stop()
    except Exception:
        traceback.print_exc()

    if _log:
        _log.info("TymelineFusion360 stopped")

    _server = None
    _bridge = None
    _handler = None
