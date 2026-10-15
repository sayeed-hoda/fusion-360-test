# Fusion 360 + Tymeline

**Date:** 2026-10-12

A small Python add-in inside Fusion, plus a process on the same machine that talks to Tymeline. The agent sees the existing Fusion commands and calls **one command at a time**. We are not adding new modeling commands.

Fusion desktop is Windows and macOS. Development and tests run on Linux. A live Fusion check has to happen on a machine that has Fusion installed.

---

## How it fits together

```text
Tymeline agent
      |
      |  WebSocket  (fusion-agent serve)
      v
fusion-agent          <-- also has a local chat: fusion-agent repl
      |
      |  TCP  127.0.0.1:9876   one command per call
      v
Fusion add-in         <-- runs inside Fusion
      |
      v
Fusion main thread    <-- the only place the Fusion API is called
```

Two programs:

| Piece | Where it runs | What it does |
|---|---|---|
| **Add-in** | Inside Fusion | Listens on `127.0.0.1:9876`. Runs each command on Fusion’s main thread. |
| **fusion-agent** | Outside Fusion | Talks to Tymeline, or runs a local chat. Sends one command to the add-in. |

The old Claude MCP server is still in the repo. It is a separate path (stdio). It is not the Tymeline front.

---

## How a command runs

1. The user starts the add-in in Fusion (Utilities → Add-Ins → Run). The text window shows `Server listening on localhost:9876`.
2. `fusion-agent` waits until that port answers.
3. The agent asks `list_tools`. That list is already in the process. It does not open a socket.
4. The agent sends `call_tool` with one name and its arguments. Example: `create_sketch` with `{"plane": "xy"}`.
5. `fusion-agent` forwards that one command to port 9876 and returns whatever Fusion answered.

Lengths stay in **centimetres**. That is Fusion’s internal unit.

Two commands are hidden from the agent and are never sent:

| Hidden | Why |
|---|---|
| `execute_code` | Runs arbitrary Python inside Fusion. |
| `delete_all` | Deletes the whole design. |

`undo` stays available. It undoes the last Fusion operation. It is not one undo for a whole plan.

---

## Two ways to talk

**Local chat** — `fusion-agent repl`

A terminal chat (GLM). You type a sentence. The model can list tools and call one published command. It does not open the Tymeline WebSocket.

**Tymeline** — `fusion-agent serve`

Dials the Tymeline gateway after port 9876 answers. If Fusion closes later, the gateway connection stays up and reports `connected: false` until the add-in is back.

| Command | What it does |
|---|---|
| `fusion-agent pair <token>` | Saves the pairing on this machine. Prints the token id and URL. Does not print the secret. |
| `fusion-agent unpair` | Deletes that pairing file. |
| `fusion-agent serve` | Connects to Tymeline. Exits if there is no pairing. |
| `fusion-agent repl` | Local chat. Waits for the add-in, then talks to the model. |

In Fusion, Design → Tymeline → **Pair with Tymeline** pastes the key and starts that connection. **Unpair Tymeline** removes it.

---

## Done

Phases 1–3 of the daemon plan are on `main`. Tests: **397 passed** (2026-10-12, no Fusion required).

- Command list published from the existing tools, minus `execute_code` and `delete_all`.
- One command per call, only to `127.0.0.1:9876`.
- Status ping: add-in up → `connected: true` and the design name. Add-in down → `connected: false`, process stays up.
- Pair, unpair, and serve. WebSocket opens only after the add-in answers.
- Hello says `cad_kind: fusion360`.
- Stub-gateway tests: `list_tools` and `call_tool` round-trip, `ping` returns `pong`, hidden commands are refused, a 401 stops retry, the pairing secret is not logged.
- Local chat: waits for port 9876, then one published command per model call. Create, read, and update commands are in the list (`create_box`, `get_scene_info`, `set_parameter`, `undo`, and the rest).

---

## Remaining

**Finish the “add-in calls the chat” wait.** The chat today dials port 9876. A new, uncommitted add-in thread dials `127.0.0.1:9877` to say “I am up”. The chat does not listen on 9877 yet, so that announce has nowhere to land. Next step is one of these:

- Make `fusion-agent repl` listen on 9877, accept that connect line, then start the chat. Modeling commands stay on 9876.
- Or drop the announce thread and keep the chat polling 9876.

**Gateway repo (phase 4).** Other repo: `autocad-agent-backend`. Accept hello with `cad_kind: fusion360`, store the session as Fusion, and offer that session `list_tools` and `call_tool`. Connect URL and auth headers stay as they are. Until this lands, the daemon can connect and answer, and the live agent will not yet ask for the catalog.

**Not in this work** (later):

- One undo for several features at once
- Millimetre conversion
- A local dev HTTP page
- Checking a live Fusion window from the Linux machine
