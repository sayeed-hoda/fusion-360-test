"""Fusion360MCP Server Package — v2 (CustomEvent bridge architecture)"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

LOG_PATH = os.path.join(os.path.expanduser("~"), "fusion360mcp.log")

_logger = logging.getLogger("fusion360mcp")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False

# Fusion may reload this package repeatedly without clearing Python's logging
# registry. Reuse one handler of each kind and remove stale duplicates.
_file_handlers = [
    handler
    for handler in _logger.handlers
    if isinstance(handler, RotatingFileHandler)
    and os.path.abspath(handler.baseFilename) == os.path.abspath(LOG_PATH)
]
if _file_handlers:
    _fh = _file_handlers[0]
    for _extra in _file_handlers[1:]:
        _logger.removeHandler(_extra)
        _extra.close()
else:
    # File handler rotates at 2 MB and keeps 3 backups.
    _fh = RotatingFileHandler(LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=3)
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    _logger.addHandler(_fh)

_stream_handlers = [
    handler
    for handler in _logger.handlers
    if type(handler) is logging.StreamHandler
    and getattr(getattr(handler, "formatter", None), "_fmt", None)
    == "[MCP] %(message)s"
]
if _stream_handlers:
    _sh = _stream_handlers[0]
    for _extra in _stream_handlers[1:]:
        _logger.removeHandler(_extra)
        _extra.close()
else:
    # Also echo to Fusion's TEXT COMMANDS.
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setLevel(logging.INFO)
    _sh.setFormatter(logging.Formatter("[MCP] %(message)s"))
    _logger.addHandler(_sh)


def get_logger(name: str = None) -> logging.Logger:
    """Return a child logger.  ``get_logger("bridge")`` → ``fusion360mcp.bridge``."""
    if name:
        return _logger.getChild(name)
    return _logger
