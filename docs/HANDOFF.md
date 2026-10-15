# Handoff — Fusion GLM chat and Tymeline daemon

**Later session:** the pairing folder and the Design toolbar button are in `docs/HANDOFF-14-OCT-26.md`. This file is still the chat / port 9877 handoff.

**Date:** 2026-10-12. **Host:** Linux. Fusion is not installed here. Do not mark a live Fusion box checked from this machine.
**Branch:** `main`, even with `origin/main`.
**Tests:** `uv run pytest -q` → **397 passed** on 2026-10-12.

Two commits are on `main`. A third change is only in the working tree.

| Commit | What it is |
|---|---|
| `8650eff` | `fusion-agent repl`. GLM chat that calls one published Fusion command at a time. |
| `15a3965` | The chat polls `127.0.0.1:9876` until the add-in answers, then opens the prompt. |
| working tree | Add-in dials `127.0.0.1:9877` to announce itself. The chat does **not** listen there yet. |

`docs/PLAN.md` is still the daemon spec (catalog, `agent_ws_v1`, phases). This file is the session handoff. The Claude MCP server (`src/fusion360_mcp/server.py`) was not changed. It is still the stdio server for Claude. It is not the chat.

## What you can run

On the Windows PC, from the unzipped repo (the folder that contains `pyproject.toml`):

```powershell
$env:Path = "C:\Users\sayeed\.local\bin;$env:Path"
$env:GLM_API_KEY = "your-key"
uv sync
uv run fusion-agent repl
```

`uv` installs to `C:\Users\sayeed\.local\bin`. A PowerShell window that was already open does not see it until that `Path` line, or until a new window.

The key is an environment variable only. Do not write it into the repo. Optional: `GLM_BASE_URL` (must end in `/v1`) and `GLM_MODEL`.

Default GLM endpoint, from `src/fusion_agent/repl.py`:

```text
https://hodamohdsayeedul--ep-glm-5-3-flash-server.us-west.modal.direct/v1/chat/completions
```

Model name `glm-5.3-flash`. Header `Authorization: Bearer <GLM_API_KEY>`.

Load the add-in in Fusion with **Utilities → Add-Ins → Scripts and Add-Ins → + → Script or add-in from device**, and choose the `addon` folder (`Fusion360MCP.py` and `Fusion360MCP.manifest` sit directly inside it). Then Run. A copied install is:

```powershell
Copy-Item -Recurse addon "$env:APPDATA\Autodesk\Autodesk Fusion 360\API\AddIns\Fusion360MCP"
```

Delete that destination first if it already exists, or PowerShell nests a second folder. Shift+S is the same dialog. The manifest has `runOnStartup` false, so Run is required until that box is checked. TEXT COMMANDS should show `[MCP] Server listening on localhost:9876`. The same line is in `%USERPROFILE%\fusion360mcp.log`.

## What the chat does

`fusion-agent repl` (`src/fusion_agent/repl.py`, also `scripts/agent_repl.py`):

1. Requires `GLM_API_KEY` or `OPENAI_API_KEY`. If missing, it exits 1 and does not touch Fusion.
2. Polls `CommandService.get_status()` every 2 seconds. That pings `127.0.0.1:9876`. It prints one waiting line, then `Fusion add-in is connected` when the ping works. Ctrl+C during the wait exits 0. GLM is not called until then.
3. Each typed line goes to GLM over HTTP. The model is given two functions: `list_tools` and `call_tool`.
4. `list_tools` reads the in-process catalog. It does not open a socket.
5. `call_tool` sends **one** command through `Fusion360Connection` to `127.0.0.1:9876`. The result is shown back to the model. A `render_view` PNG is an image part; the base64 is not repeated in the text.
6. Lengths stay in centimetres. `/quit` leaves.

The catalog is `TOOLS` in `src/fusion360_mcp/tools.py` minus `execute_code` and `delete_all`. Create, read, and update commands are in that list (`create_box`, `get_scene_info`, `rename_body`, `set_parameter`, `undo`, `delete_parameter`, and the rest). `delete_all` and `execute_code` are refused and are not sent to Fusion.

This is not a WebSocket. `fusion-agent serve` is the other command. That one dials the Tymeline gateway and **does** wait for port 9876 before it opens the socket. The chat does not start `serve`.

## Open mismatch

The user asked for the FreeCAD shape: start the chat, it waits, the add-in connects to it. The committed chat does the opposite. It dials port 9876, so a refused connection is logged (`Failed to connect`) on each poll.

Uncommitted files try the other direction and are not finished:

| File | State |
|---|---|
| `addon/server/repl_announce.py` | New. Connects to `127.0.0.1:9877` and sends one JSON line: `{"type":"connect","cad_kind":"fusion360","command_port":9876}`. Retries every 2s until the chat accepts it or the add-in stops. |
| `addon/Fusion360MCP.py` | `run` starts that announce thread after the 9876 server starts. `stop` sets the stop event. |

`src/fusion_agent/repl.py` does **not** listen on 9877. Nothing accepts that connect line. Do not tell a user the chat is waiting for the add-in to dial in. Finish the listener in the chat, or remove the announce thread before shipping the add-in.

Modeling commands stay on 9876 either way. The 9877 socket is only a “the add-in is up” signal. `src/fusion360_mcp/server.py` was not pointed at the chat.

## Daemon (already on main)

`fusion-agent` in `pyproject.toml` → `fusion_agent.cli:main`.

| Command | Behavior |
|---|---|
| `pair <TYM1...>` | Writes `pairing.json` under `%APPDATA%\tymeline-fusion360` (Windows), `~/Library/Application Support/tymeline-fusion360` (macOS), or `$XDG_CONFIG_HOME/tymeline-fusion360` / `~/.config/tymeline-fusion360` (Linux). Mode `0600` on macOS and Linux. Prints `token_id` and url. Does not print the secret. Untrusted host exits 2. |
| `unpair` | Deletes the file. |
| `serve` | WebSocket to the gateway after port 9876 answers. No pairing and no env pair exits 1. Does not bind 9876. |
| `repl` | The GLM chat above. |

Gateway URL trust, headers, backoff, and hello shape are in `docs/PLAN.md` sections 5 and 6. `src/fusion_agent/core/` is only `errors.py`. Do not add `apply_plan` or `fusion_ops_v1` to finish the chat.

`tests/test_stub_gateway.py` is on `main`. It covers a fake gateway round trip and a 401 that stops retry without logging the secret. Phase 4 (the gateway repo `~/Professional/tymeline/autocad-agent-backend`) was not started.

## Left alone

- `src/fusion360_mcp/server.py` (Claude stdio MCP)
- One undo group for several features, `apply_plan`
- Millimetre conversion
- Dev HTTP on `127.0.0.1:41829`
- `autodesk-fusion-mcp/`
- Live Fusion checks from this Linux machine

## What the next agent should do first

1. Decide the wait. The user wants the chat to listen and the add-in to connect, then a normal sentence should create, read, update, or delete through the published commands.
2. If that is still the goal: make `fusion-agent repl` listen on `127.0.0.1:9877`, accept the `type: connect` line from `repl_announce.py`, and only then talk to GLM. Keep command calls on `127.0.0.1:9876`. Add a test that does not bind 9876.
3. If the poll on 9876 is enough: revert `addon/Fusion360MCP.py` and delete `addon/server/repl_announce.py` so the add-in does not dial a port nothing listens on.
4. The Windows zip the user ran (`fusion-360-test-main`) will not have either fix until those files are copied into that folder and the add-in is stopped and started again in Fusion.
