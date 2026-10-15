"""Start the Tymeline daemon from inside Fusion.

Fusion's Python does not run the gateway client. This module spawns the
separate ``fusion-agent`` program. The add-in does not choose the pairing
folder: ``pair`` writes the key, and ``serve`` reads that same file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Fusion's embedded interpreter must not become the daemon's Python.
_PYTHON_ENV_KEYS = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONEXECUTABLE",
    "PYTHONNOUSERSITE",
    "PYTHONSAFEPATH",
    "PYTHONUSERBASE",
)

# Windows: a console-subsystem fusion-agent.exe would open a command prompt
# when Fusion starts it. CREATE_NO_WINDOW suppresses that window.
# DETACHED_PROCESS is not combined with it: Windows ignores CREATE_NO_WINDOW
# when DETACHED_PROCESS is set, and the command prompt comes back.
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_SW_HIDE = 0


class AgentLaunchError(Exception):
    """The daemon program could not pair or start. The text is safe to show."""


def project_root(start: Path | None = None) -> Path | None:
    """Directory that contains this repo's ``src/fusion_agent`` package."""
    env = os.environ.get("FUSION_AGENT_PROJECT", "").strip()
    if env and _is_project(Path(env)):
        return Path(env)
    here = start if start is not None else Path(__file__).resolve()
    for parent in (here, *here.parents):
        if _is_project(parent):
            return parent
    return None


def agent_command(start: Path | None = None) -> list[str] | None:
    """Argv up to, but not including, ``pair`` / ``serve`` / ``stop``."""
    env_bin = os.environ.get("FUSION_AGENT_BIN", "").strip()
    if env_bin and os.access(env_bin, os.X_OK):
        return [env_bin]
    found = shutil.which("fusion-agent")
    if found:
        return [found]
    root = project_root(start)
    if root is None:
        return None
    if os.name == "nt":
        venv_bin = root / ".venv" / "Scripts" / "fusion-agent.exe"
    else:
        venv_bin = root / ".venv" / "bin" / "fusion-agent"
    if os.access(venv_bin, os.X_OK):
        return [str(venv_bin)]
    uv = shutil.which("uv")
    if uv is not None and (root / "pyproject.toml").is_file():
        return [uv, "run", "--project", str(root), "fusion-agent"]
    return None


def subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for ``fusion-agent``, without Fusion's Python home."""
    env = dict(os.environ if base is None else base)
    for key in _PYTHON_ENV_KEYS:
        env.pop(key, None)
    return env


def spawn_kwargs(os_name: str | None = None) -> dict[str, int | bool | object]:
    """Start ``fusion-agent`` with no command prompt, and keep ``serve`` alive."""
    if (os.name if os_name is None else os_name) == "nt":
        kwargs: dict[str, int | bool | object] = {
            "creationflags": _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        }
        if os.name == "nt":
            info = subprocess.STARTUPINFO()
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = _SW_HIDE
            kwargs["startupinfo"] = info
        return kwargs
    return {"start_new_session": True}


def pair_and_start(
    token: str,
    *,
    start: Path | None = None,
    run: object | None = None,
    popen: object | None = None,
    log_file: Path | None = None,
) -> str:
    """Store a pairing key with ``fusion-agent pair``, then start ``serve``."""
    secret = token.strip()
    if not secret:
        raise AgentLaunchError("Paste the whole TYM1 key.")
    command = _require_command(start)
    runner = run if run is not None else subprocess.run
    paired = _run(
        runner,
        [*command, "pair", secret],
        secret,
        timeout=30,
        failure="Pairing failed.",
    )
    _run(
        runner,
        [*command, "stop"],
        secret,
        timeout=10,
        failure="The previous connection is still running.",
    )
    started = _start_serve(command, secret, popen=popen, log_file=log_file)
    return f"{paired} {started}".strip()


def unpair(
    *,
    start: Path | None = None,
    run: object | None = None,
) -> str:
    """Forget the key with ``fusion-agent unpair``, then stop the connection."""
    command = _require_command(start)
    runner = run if run is not None else subprocess.run
    text = _run(
        runner,
        [*command, "unpair"],
        "",
        timeout=30,
        failure="Unpair failed.",
    ).lower()
    _run(
        runner,
        [*command, "stop"],
        "",
        timeout=10,
        failure="The connection is still running.",
    )
    if "not paired" in text:
        return "Tymeline was not paired."
    return "Tymeline pairing removed."


def _require_command(start: Path | None) -> list[str]:
    command = agent_command(start)
    if command is None:
        raise AgentLaunchError(
            "fusion-agent was not found. Set FUSION_AGENT_BIN to the program "
            "in this repo's .venv folder (Scripts on Windows, bin on macOS), "
            "or run the add-in from the repo's addon folder."
        )
    return command


def _run(
    runner: object, argv: list[str], secret: str, *, timeout: int, failure: str
) -> str:
    proc = runner(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=subprocess_env(),
        **spawn_kwargs(),
    )
    combined = "\n".join(
        part.strip() for part in (proc.stdout or "", proc.stderr or "") if part.strip()
    )
    visible = _redact(combined or failure, secret).strip()
    if proc.returncode != 0:
        raise AgentLaunchError(visible or failure)
    return visible


def _start_serve(
    command: list[str],
    secret: str,
    *,
    popen: object | None,
    log_file: Path | None,
) -> str:
    log_dest = log_file if log_file is not None else Path.home() / "fusion360mcp.log"
    spawn = popen if popen is not None else subprocess.Popen
    try:
        log_dest.parent.mkdir(parents=True, exist_ok=True)
        log = log_dest.open("a", encoding="utf-8")
    except OSError as exc:
        raise AgentLaunchError(f"Tymeline connection did not start: {exc}") from exc
    try:
        spawn(
            [*command, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            close_fds=True,
            env=subprocess_env(),
            **spawn_kwargs(),
        )
    except OSError as exc:
        log.close()
        raise AgentLaunchError(
            _redact(f"Tymeline connection did not start: {exc}", secret)
        ) from exc
    log.close()
    return "Tymeline connection is starting."


def _redact(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, "[redacted]")


def _is_project(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() and (
        path / "src" / "fusion_agent" / "cli.py"
    ).is_file()
