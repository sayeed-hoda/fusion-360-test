# Handoff — pairing folder and the Pair button

**Date:** 2026-10-14. **Host:** Linux. Fusion is not installed here. Do not mark a live Fusion box checked from this machine.
**Branch:** `main`, even with `origin/main`. Nothing from this session is committed.
**Tests:** `uv run pytest -q` → **415 passed**. No test opens a socket to port 9876. No test imports `adsk`.

The chat / port 9877 notes are still `docs/HANDOFF.md`. This file is the pairing work. `docs/PAIRING-PATH.md` is the folder spec. It is implemented. Do not start that spec again from `docs/PLAN.md`.

## What changed

The Tymeline key follows the operating system. `config_root()` in `src/fusion_agent/transport/paths.py` is the only function that chooses the folder. `pairing.json`, `device_id`, and `agent.pid` stay inside it.

| System | Folder |
|---|---|
| Windows | `%APPDATA%\tymeline-fusion360\` |
| macOS | `~/Library/Application Support/tymeline-fusion360/` |
| Linux | `$XDG_CONFIG_HOME/tymeline-fusion360/` or `~/.config/tymeline-fusion360/` |

`FUSION_AGENT_CONFIG_DIR`, when set, replaces that whole folder. Tests use it. A normal install does not.

On Windows and macOS, a key already saved under `~/.config/tymeline-fusion360/` is copied once into the new folder, along with `device_id`. The old files stay. `agent.pid` is not copied. The copy does not run when `FUSION_AGENT_CONFIG_DIR` is set, and it does not run on Linux.

`fusion-agent pair` still writes the file mode `0600`. That mode is the privacy boundary on macOS and Linux. On Windows the user profile is the boundary. Do not describe the Windows file as mode `0600`.

The Design workspace has a **Tymeline** panel:

| Button | What it runs |
|---|---|
| Pair with Tymeline | A dialog asks for the `TYM1.` key. OK is enabled only when the text starts with `TYM1.`. Then `fusion-agent pair`, `fusion-agent stop`, `fusion-agent serve`. |
| Unpair Tymeline | Confirms, then `fusion-agent unpair` and `fusion-agent stop`. |

`stop` is there so a second paste replaces the connection that is already running. `serve` is started detached and appends to `~/fusion360mcp.log`. The message box shows the token id and URL. It does not show the secret.

The add-in does not build a pairing path. `addon/server/agent_launch.py` only spawns `fusion-agent`. `addon/server/pairing_button.py` is the Fusion dialog. It imports `adsk`, so the unit tests load `agent_launch.py` by path and never import the button module.

## Files

| File | State |
|---|---|
| `src/fusion_agent/transport/paths.py` | `config_root()`, legacy copy. |
| `src/fusion_agent/cli.py` | `fusion-agent stop`. |
| `addon/server/agent_launch.py` | New. Finds `fusion-agent` and runs pair / stop / serve / unpair. |
| `addon/server/pairing_button.py` | New. Design workspace panel `TymelineFusionPanel`. |
| `addon/Fusion360MCP.py` | `run` installs the buttons after the 9876 server starts. `stop` removes them. A button failure does not stop the socket. |
| `tests/test_agent_launch.py` | New. Pair then stop then serve. A rejected key does not start serve. The secret is redacted. |
| `tests/test_fusion_agent_cli.py` | Folder tests, legacy copy, `stop`. The mode `0600` check stays on POSIX only. |

`src/fusion360_mcp/server.py` was not changed. It is still the Claude stdio server.

## How the button finds fusion-agent

In this order:

1. `FUSION_AGENT_BIN`, if that file is executable.
2. `fusion-agent` on `PATH`.
3. This repo’s `.venv` (`Scripts\fusion-agent.exe` on Windows, `bin/fusion-agent` on macOS and Linux). The repo is `FUSION_AGENT_PROJECT`, or the first parent of the add-in that contains `pyproject.toml` and `src/fusion_agent/cli.py`.
4. `uv run --project <that repo> fusion-agent`, when `uv` is on `PATH`.

Fusion’s `PYTHONHOME` and `PYTHONPATH` are removed from the child environment so Fusion’s interpreter is not the one that runs the daemon.

Loading the add-in from this repo’s `addon` folder finds the `.venv` by walking parents. A copy under `%APPDATA%\Autodesk\...\AddIns\` does not. On that copy, set `FUSION_AGENT_BIN` to the `.venv` program before starting Fusion. The dialog says the same thing when the program is missing.

## What to check on Windows or macOS

Fusion was not started here. After copying this working tree into the add-in folder, stop the add-in and run it again.

1. Design workspace. A **Tymeline** panel. **Pair with Tymeline** on the toolbar.
2. Paste a real `TYM1.` key. The message shows `token_id` and the URL, and does not show the secret.
3. The file is `%APPDATA%\tymeline-fusion360\pairing.json` on Windows, or `~/Library/Application Support/tymeline-fusion360/pairing.json` on macOS. Not under the add-in folder, and not under `~/.config` unless that was already the OS folder.
4. If a key was left in `~/.config/tymeline-fusion360/` from an older build, the new folder receives `pairing.json` and `device_id` once. The old copies remain. `agent.pid` does not move.
5. `~/fusion360mcp.log` shows `fusion-agent serve` starting. TEXT COMMANDS shows `[MCP]` lines for the button result.
6. Unpair removes the key and stops the connection. Pair again and confirm the new key is the one that connects.
7. An untrusted host is refused and no file is written.

## Still open

From `docs/MORNING-14-OCT-26.md`, not started in this session:

- Fusion 360 Desktop support in the integration service.
- The frontend add-on flow.
- Forward `list_tools` and `call_tool` on the live gateway socket. A read of the open design is `call_tool` with `get_scene_info`.
- A live pass of the REPL and the plugin, including the dialog above.

From `docs/HANDOFF.md`, still true: `addon/server/repl_announce.py` dials `127.0.0.1:9877`, and `fusion-agent repl` does not listen there. This session did not finish or remove that thread. `run` still starts it, then installs the Pair button.
