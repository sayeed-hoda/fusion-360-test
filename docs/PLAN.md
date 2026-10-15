# Fusion 360 — connect to Tymeline and publish the MCP commands

**Date:** 2026-10-12.
**Status:** plan only. Do not start from the undo phases in `~/Professional/tymeline/fusion360/PLAN.md`.
**Repo:** `fusion360-mcp-server/`. Fork of Faust Network, MIT, commit `19b9f1e`. Keep `LICENSE`.

This is the work. The plugin connects to Tymeline. After it connects, the agent asks which commands exist, reads each command's name, description, and parameters, and calls one command at a time. The add-in already has those commands. This plan does not add modeling commands and does not add one undo step for a whole plan.

FreeCAD is the copy for the socket: `~/Professional/tymeline/freecad-plugin/freecad-mcp/src/freecad_agent/transport/`. FreeCAD's command list is `scripts/agent_repl.py` `list_tools`, which reads the MCP catalog and hides `execute_code`. Fusion does the same with `src/fusion360_mcp/tools.py`.

KiCad had no MCP server, so its plugin speaks a fixed op list inside one board commit. Do not copy that commit. Copy only KiCad's socket files if the FreeCAD copies are missing a helper.

Fusion desktop is Windows and macOS. This machine is Linux. Write the daemon and the tests here. Do not mark a live Fusion box checked from this machine.

---

## 1. What already exists

```text
Today
  Claude --stdio MCP--> src/fusion360_mcp/server.py
                            |
                            TCP 127.0.0.1:9876
                            |
                       addon (Fusion main thread)

Add
  Tymeline gateway --agent_ws_v1--> fusion daemon
                                        |
                                        TCP 127.0.0.1:9876
                                        |
                                   addon (one existing command per call)
```

| Piece | Where | Use it? |
|---|---|---|
| Add-in listener | `addon/Fusion360MCP.py`, port **9876**, newline JSON | Yes. Leave the protocol. |
| TCP client | `src/fusion360_mcp/connection.py` `Fusion360Connection` | Yes. The daemon calls this. |
| MCP catalog | `src/fusion360_mcp/tools.py` `TOOLS` | Yes. This is the command list. |
| Main thread | `addon/server/event_bridge.py` | Yes. Already queues work. Do not change the timeout rules for this plan. |
| Claude MCP server | `src/fusion360_mcp/server.py` | Leave it in the tree. It is not the Tymeline front. |
| `apply_plan`, `plan_exec.py`, `fusion_ops_v1` | addon and `src/fusion_agent/core/` | Leave the files. The daemon does not call them. |

The daemon binds nothing on 9876. That port is the add-in. A later dev HTTP page, if added, uses `127.0.0.1:41829`.

---

## 2. What the agent does

1. The daemon dials out and sends hello with `cad_kind: "fusion360"`.
2. The agent sends `list_tools`.
3. The daemon returns every public MCP tool: `name`, `description`, `parameters`. `parameters` is that tool's `inputSchema`.
4. The agent sends `call_tool` with one name and an arguments object from that schema.
5. The daemon sends that one command to the add-in and returns the result.

Lengths stay in centimetres. That is what the existing schemas already document. Do not convert to millimetres in this plan.

`list_tools` does not open a socket. The catalog is in the process. `call_tool` is the only command that talks to Fusion.

---

## 3. Commands on the gateway socket

| `type` | What the daemon does |
|---|---|
| `get_status` | Ping `127.0.0.1:9876`. If it answers, `connected` is true and `document` is `design_name` from `get_scene_info`. If Fusion is closed, `connected` is false and the daemon stays up. |
| `list_tools` | Return the catalog from section 4. |
| `call_tool` | Run one allowed tool. |
| `ping` | Reply `{"type":"pong"}`. |
| anything else | `not_supported`. This includes `apply_plan`, `execute_code`, and `delete_all`. |

`call_tool` payload:

```json
{ "name": "create_sketch", "arguments": { "plane": "xy" } }
```

Missing `name` is `bad_request`. `arguments` must be an object. A missing `arguments` means `{}`.

Unknown name, `execute_code`, or `delete_all` is `not_supported`. Do not send those to the add-in.

A socket timeout or `error_kind` `TIMEOUT` from the add-in is `fusion_busy`. Do not retry a command that changes the model. `Fusion360Connection` already skips that retry when the command is in its mutation set.

Success:

```json
{ "success": true, "name": "create_sketch", "result": { } }
```

`result` is whatever the add-in returned for that one command.

---

## 4. Catalog

Read `TOOLS` in `src/fusion360_mcp/tools.py`. Publish every entry except these two:

| Hidden name | Why |
|---|---|
| `execute_code` | Runs arbitrary Python inside Fusion. |
| `delete_all` | Deletes the whole design. |

Both stay on the add-in's old command table. `CommandService` has no branch that sends them.

Each published item:

```json
{
  "name": "create_sketch",
  "description": "Create a new sketch on xy/yz/xz plane, optionally offset",
  "parameters": { "type": "object", "properties": { } }
}
```

`parameters` is the tool's `inputSchema`, unchanged. The response envelope is:

```json
{ "schema": "fusion_mcp_tools_v1", "tools": [ ] }
```

`undo` stays in the catalog. It is one existing add-in command. It is not a transaction around several commands.

---

## 5. Hello and status

Hello, after the socket is open and port 9876 has answered:

```json
{
  "type": "event",
  "event": "hello",
  "payload": {
    "protocol_version": "agent_ws_v1",
    "device_id": "stable id for this machine",
    "plugin_version": "0.1.0",
    "cad_kind": "fusion360",
    "fusion_version": "2605.0.0",
    "supported_schemas": ["fusion_mcp_tools_v1"],
    "document": "Untitled",
    "confirmation_required": false
  }
}
```

Omit `fusion_version` and `document` when status does not have them. `device_id` is created once and reused.

`get_status` payload:

```json
{
  "schema": "fusion_status_v1",
  "cad_kind": "fusion360",
  "plugin_version": "0.1.0",
  "document": "Untitled",
  "connected": true,
  "supported_schemas": ["fusion_mcp_tools_v1"]
}
```

`connected: false` means Fusion or the add-in is down. The daemon still answers.

Error codes already in `src/fusion_agent/core/errors.py`: `bad_request`, `not_supported`, `no_document`, `stale_plan`, `fusion_busy`, `internal_error`. This plan uses `bad_request`, `not_supported`, `fusion_busy`, and `internal_error`.

---

## 6. Connection rules

Copy these FreeCAD files into `src/fusion_agent/transport/` and change the names below. Do not copy `freecad_agent/service.py`.

| Copy from `freecad-mcp/src/freecad_agent/transport/` | Fusion change |
|---|---|
| `pairing.py` | Store `pairing.json` in `%APPDATA%\tymeline-fusion360` (Windows), `~/Library/Application Support/tymeline-fusion360` (macOS), or `$XDG_CONFIG_HOME/tymeline-fusion360` / `~/.config/tymeline-fusion360` (Linux). Mode `0600` on macOS and Linux. Env names `FUSION_AGENT_GATEWAY`, `FUSION_AGENT_TOKEN`, `FUSION_AGENT_TOKEN_ID`. User text says `fusion-agent pair`. |
| `device_id.py` | Same file location, under `tymeline-fusion360`. If the directory cannot be created, still return an id so hello can be sent. |
| `backoff.py` | Copy as-is. 1s, double, cap 60s. Command timeout 120s. |
| `ws_client.py` | `cad_kind` `fusion360`. "Is Fusion up?" calls `CommandService.get_status()` and continues only when `connected` is true. Poll every 20s. Dispatch section 3. Rename the FreeCAD wait/watch helpers to Fusion. |

Pairing, same as FreeCAD:

- Token text starts with `TYM1.`
- Payload is base64url JSON: `{url, token_id, secret, display}`
- Before connecting, trust loopback, or `wss://` whose host is `tymeline.app` or a subdomain of it
- Connect path: `/api/v1/integrations/plugin/connect`
- Headers: `Authorization: Bearer <secret>`, and `X-Tymeline-Token-Id` when `token_id` is present
- HTTP 401: stop retrying
- Close reason containing `hello required`: stop retrying
- Other failures: wait 1s, double up to 60s, keep retrying. Stay quiet after 3 failures
- `ws://` is allowed only for loopback
- Do not log the secret

Do not open the WebSocket until ping on `127.0.0.1:9876` succeeds. If Fusion closes later, leave the gateway socket up, answer `get_status` with `connected: false`, fail `call_tool` until ping works, then send hello again.

The WebSocket loop stays async. Each command runs on a worker thread. The add-in call stays synchronous. That split is `run_in_executor` in FreeCAD's `_handle`.

`websockets>=12` is a daemon dependency. It is already listed in `pyproject.toml`. Do not import it from `addon/`.

Host for the add-in client is `127.0.0.1` only. Ignore `FUSION_MCP_HOST`.

---

## 7. Process entry

Add `fusion-agent` in `pyproject.toml` scripts, module `fusion_agent.cli`.

| Command | Behavior |
|---|---|
| `fusion-agent pair <TYM1...>` | Parse the token, refuse an untrusted host, write the pairing file. Print `token_id` and url. Do not print the secret. |
| `fusion-agent unpair` | Delete the pairing file. |
| `fusion-agent serve` | Start the WebSocket when pairing or the env pair is present and trusted. Exit with a message when it is not. One process at a time. |

`serve` does not require the dev HTTP server. Port 41829 is reserved and unused in this plan.

---

## 8. Tymeline side — the API key and where the plugin talks

Phases 1–3 are the Fusion daemon in this repo. Phase 4 is the other repo: `~/Professional/tymeline/microservice/tymeline-integration-srv`. Copy the FreeCAD Desktop integration. The daemon already follows the socket rules in section 6. This phase makes Tymeline mint the key, accept the socket, remember the workstation, and let an AI Employee ask that live session for its tools.

The URL baked into the key is this service:

```text
wss://<tymeline-host>/api/v1/integrations/plugin/connect
```

That route is `GET /integrations/plugin/connect` in `pkg/api/routes/integration.go`. AutoCAD, KiCad, and FreeCAD already use one env value for it:

```text
AUTOCAD_DESKTOP_CONNECT_URL=wss://staging-new-tools.tymeline.app/api/v1/integrations/plugin/connect
```

A Fusion key uses that same value. `autocad-agent-backend` is a different process and a different path (`/v1/plugin/connect`). A key minted here dials the integration service.

### 8.1 What the API key is

The string a person pastes into Fusion starts with `TYM1.`. It is the pairing key. It is not sent again on each command.

`pkg/services/plugintoken/token.go` builds it. After `TYM1.`, the rest is base64url JSON:

```json
{
  "url": "wss://staging-new-tools.tymeline.app/api/v1/integrations/plugin/connect",
  "token_id": "uuid of the token row",
  "secret": "32 random bytes, base64url",
  "display": "person@company.com"
}
```

| Field | Who uses it |
|---|---|
| `url` | The daemon dials this. The path is already in it. |
| `token_id` | Header `X-Tymeline-Token-Id`. Safe to log and to show in the UI. |
| `secret` | Header `Authorization: Bearer <secret>` on the WebSocket upgrade only. |
| `display` | A label the plugin can show. Tymeline does not use it to decide the tenant. |

Mongo stores `sha256(secret)` as hex (`SecretSHA256`). The raw `TYM1.` string is returned to the browser once and is not stored. `GET .../plugin-tokens` lists id, label, and whether a workstation is online. It does not return the key.

The tenant (`business_id`, `account_id`, `integration_id`) comes from the token row. The hub looks up `token_id`, checks the secret hash with `plugintoken.VerifySecret`, and answers HTTP 401 for an unknown id, a revoked or expired row, and a wrong secret. Those three look the same on the wire. The log line says which one it was.

### 8.2 How a person gets the key

The browser calls these with the normal Tymeline admin bearer (role admin). The Fusion plugin never calls them. The handlers already exist for AutoCAD, KiCad, and FreeCAD. Fusion is another branch in `pkg/api/handler/autocad_desktop.go`, same shape as `useFreeCAD`.

| Call | What it does |
|---|---|
| `POST /api/v1/integrations/{integration_id}/integrate` | Creates the Fusion 360 Desktop connection and mints the key in that same call. The response includes `token` (the `TYM1.` string), `token_id`, and `connect_url`. |
| `POST /api/v1/integrations/{integration_id}/plugin-tokens` | Mints a replacement key after the current one is revoked. Body is optional: `label`, `expires_in_days`. |
| `GET /api/v1/integrations/{integration_id}/plugin-tokens` | Lists keys for this connection. No secret. |
| `DELETE /api/v1/integrations/{integration_id}/plugin-tokens/{token_id}` | Revokes the key and drops the live socket that used it. |
| `GET /api/v1/integrations/{integration_id}/plugin-devices` | Workstations that connected with this connection's key. |
| `POST /api/v1/integrations/{integration_id}/sync` | Liveness only. Section 8.5. |

Copy `SaveFreeCADDesktopConnection` in `pkg/usecase/connection.go`. There is no remote password to check. The call writes a credential row that holds `label` and `connect_url` only, then calls `CreatePluginTokenForConnection`. The token row is saved before the raw key is returned, so a key whose id is missing cannot pass the upgrade. If the mint fails, delete the credential row that was just written. Status starts as waiting for the plugin. Connecting again on an existing connection keeps the old key and does not mint a second one.

One usable key per connection. A second mint while the first is still usable returns an error that says to revoke first.

Then tell workforce this business has a design tool. Workforce matches the slug `fusion360` exactly. Send that slug. `Fusion360Desktop` creates no tool.

Marketplace tile id for the populate script: `a542fbaa-543b-4a37-88d1-a019b7f1d6a8`.

### 8.3 What the Fusion machine does with the key

The designer pastes the `TYM1.` string into `fusion-agent pair` (section 7). The daemon decodes it and writes `pairing.json` in `%APPDATA%\tymeline-fusion360` on Windows, `~/Library/Application Support/tymeline-fusion360` on macOS, or `$XDG_CONFIG_HOME/tymeline-fusion360` / `~/.config/tymeline-fusion360` on Linux (mode `0600` on macOS and Linux). It opens one WebSocket:

```text
GET <url from the key>
Authorization: Bearer <secret>
X-Tymeline-Token-Id: <token_id>
```

`DesktopPluginBridge.AuthorizeConnect` (`pkg/usecase/desktop_plugin_bridge.go`) must find this `token_id` in the Fusion token collection and call the Fusion `AuthorizeConnect`. FreeCAD is already a branch in that switch. Fusion is the next branch. On success the hub upgrades the socket and reads hello (section 5): `cad_kind: "fusion360"`, `fusion_version`, `supported_schemas: ["fusion_mcp_tools_v1"]`.

Write a device row from that hello: device id, document, plugin version, fusion version, supported schemas, connected time. The tile moves from waiting to connected. When Fusion later closes, the gateway socket can stay up and `get_status` reports `connected: false` (section 6). The device row follows the existing heartbeat. FreeCAD treats 90 seconds without a frame as stale.

The key is finished at the upgrade. Commands after that travel on the open socket and do not carry the secret.

### 8.4 How a read of the design works

This is the Fusion equivalent of extract. The AI Employee does not dial Fusion, and it does not send the `TYM1.` key. Another Tymeline service calls this integration service over gRPC (`pkg/api/handler/grpc/plugin_service.go`):

```text
ListPluginDevices        business_id
AcquireDeviceLease       only when the command changes the model
ExecutePluginCommand     business_id, device_id, command, json payload
ReleaseDeviceLease
```

`ExecutePluginCommand` finds the live socket for that device and forwards the command. `knownCommands` in `pkg/services/plugingateway/command.go` currently forwards `get_status`, `extract`, `apply_plan`, `export`, and `run_checks`.

The Fusion daemon answers `extract` and `apply_plan` with `not_supported` (section 3). Add `list_tools` and `call_tool` to the allowlist and forward the payload unchanged.

| Command | Payload | Lease |
|---|---|---|
| `list_tools` | `{}` | No. Catalog from section 4. Does not open Fusion. |
| `get_status` | `{}` | No. Already allowed. |
| `call_tool` | `{ "name": "<tool>", "arguments": { } }` | Yes, except the read names below. |

A read of the open design is:

```json
{ "name": "get_scene_info", "arguments": {} }
```

One object is `get_object_info`. The names and their `parameters` come from `list_tools`. These `call_tool` names are reads and do not take a lease: `get_scene_info`, `get_object_info`, `get_bounding_box`, `get_parameters`, `list_components`. Every other name takes a lease, the same way `apply_plan` does today.

`GetIntegrationCapabilities` for platform `Fusion360Desktop` returns `list_tools` and `call_tool`, schema `fusion_mcp_tools_v1`. FreeCAD never added a manifest; Fusion needs one, because an empty manifest leaves the caller on `extract`.

### 8.5 What sync does

`doSyncForFreeCADDesktop` does not download a model. It marks devices stale after 90 seconds without a frame and updates the tile. Fusion sync is that function with Fusion names. Model data moves only as `call_tool` on the live socket.

### 8.6 Files to add, by copying FreeCAD

Leave the FreeCAD files in place. Copy them and rename FreeCAD to Fusion 360.

| Copy | Change |
|---|---|
| `pkg/domain/freecad_desktop.go` | `Fusion360DesktopToken`, `Fusion360DesktopDevice`. Store `fusion_version` from hello. |
| `pkg/domain/integration.go` | `IntegrationPlatformFusion360Desktop = "Fusion360Desktop"`. `WorkforceToolPlatformFusion360 = "fusion360"`. Add it in `WorkforceDesignToolPlatform`. |
| `pkg/models/freecad_desktop.go` | Credential struct. No secret fields. |
| `pkg/repository/freecad_desktop.go` | Same queries against the new collections. |
| `pkg/repository/const.go` | `integration_fusion360_desktop_tokens`, `integration_fusion360_desktop_devices`. |
| `pkg/usecase/freecad_desktop.go` | Mint, list, revoke, `AuthorizeConnect`, device connect and disconnect. `ConnectURL()` returns `cfg.AutoCADDesktopConnectURL`. |
| `pkg/usecase/freecad_desktop_sync.go` | 90 second liveness. |
| `pkg/usecase/connection.go` | `SaveFusion360DesktopConnection`, called from the integrate switch beside FreeCAD. |
| `pkg/usecase/integration.go` | Construct the use case. Disconnect deletes that connection's tokens and devices. |
| `pkg/usecase/sync.go` | `IntegrationPlatformFusion360Desktop` runs the liveness sync. |
| `pkg/usecase/desktop_plugin_bridge.go` | Branch beside FreeCAD for authorize, connect, disconnect, seen, and stale. |
| `pkg/api/handler/autocad_desktop.go` | `useFusion` on the existing plugin-token and plugin-device handlers. |
| `pkg/di/plugin_gateway.go` and wire | Pass the Fusion use case into the bridge. |
| `pkg/db/integration_populate.go` | Tile id `a542fbaa-543b-4a37-88d1-a019b7f1d6a8`, in the same category as FreeCAD Desktop. Instructions: install the Fusion add-in, choose Pair, paste the `TYM1.` key. |
| `pkg/common/integration_logos.go` | Icon URL. |
| `pkg/config` | Optional `FUSION360_DESKTOP_PLUGIN_DOWNLOAD_URL`, same role as the FreeCAD download URL on the mint response. |
| `pkg/services/plugingateway/envelope.go` | `fusion_version` on hello, beside `freecad_version`. |
| `pkg/services/plugingateway/command.go` | Allow `list_tools` and `call_tool`. Lease rule in section 8.4. |
| `pkg/services/plugingateway/capabilities.go` | Manifest for `Fusion360Desktop`. |

The connect path, the two headers, and `AUTOCAD_DESKTOP_CONNECT_URL` stay as they are.

### 8.7 Hello on that hub

Accept the hello from section 5 and store `cad_kind: "fusion360"`. An empty `cad_kind` stays AutoCAD, which is already the hub's rule. A Fusion session is offered `list_tools` and `call_tool`.

---

## 9. Tests on this machine

No Fusion. No `adsk` import. A unit test must not open a real socket to port 9876.

| Test | Proves |
|---|---|
| Catalog | Published names equal `TOOLS` minus `execute_code` and `delete_all`. `create_sketch` includes its parameter schema. |
| `list_tools` | Does not call the fake connection. |
| `call_tool` | `create_sketch` with `{"plane":"xy"}` is one `send_command`. The result is returned. |
| Rejected tools | `execute_code`, `delete_all`, and a missing name do not call the fake connection. |
| Status | Ping success includes `document`. Ping failure is `connected: false`. |
| Host | Constructing the service with a non-loopback host raises. |
| Stub gateway | Hello has `cad_kind` `fusion360` and `supported_schemas` `["fusion_mcp_tools_v1"]`. `list_tools` and `call_tool` round-trip. `ping` returns `pong`. `execute_code` as a socket type is `not_supported`. |
| 401 | Retry stops. The secret string does not appear in the status lines. |

`src/fusion_agent/core/` tests that already exist must stay green. Do not point them at the socket.

---

## 10. Phases

Do these in order. A later phase starts only after the one before it is done. Say which phase to start.

### Phase 1 — Catalog and CommandService

No WebSocket. No live Fusion. A test must not open a real socket to port 9876.

- Read `TOOLS` in `src/fusion360_mcp/tools.py`. Return `{ "schema": "fusion_mcp_tools_v1", "tools": [ ] }`. Each item is `name`, `description`, and `parameters` (`inputSchema`, unchanged). Omit `execute_code` and `delete_all`.
- `CommandService`: `list_tools` returns that catalog and does not call the connection. `call_tool` sends one allowed command through `Fusion360Connection` and returns the add-in result. `get_status` pings `127.0.0.1:9876`.
- Missing `name` is `bad_request`. Missing `arguments` means `{}`. Unknown name, `execute_code`, `delete_all`, and `apply_plan` are `not_supported` and are not sent. A timeout is `fusion_busy`. Any host other than `127.0.0.1` raises.
- Tests from section 9: Catalog, `list_tools`, `call_tool`, Rejected tools, Status, Host. Use a fake connection.

Done when those six tests pass. Leave `src/fusion_agent/core/` alone.

### Phase 2 — Transport and `fusion-agent serve`

- Copy section 6 into `src/fusion_agent/transport/`: `pairing.py`, `device_id.py`, `backoff.py`, `ws_client.py`.
- Add `fusion-agent` in `pyproject.toml`, module `fusion_agent.cli`: `pair`, `unpair`, `serve`.
- Open the WebSocket only after ping on `127.0.0.1:9876` succeeds. If Fusion closes later, leave the gateway socket up.

Done when `serve` starts from a trusted pairing or env pair, and exits with a message when neither is present.

### Phase 3 — Stub gateway

Tests from section 9: Stub gateway, and 401. No live Tymeline gateway.

Done when hello shows `cad_kind` `fusion360` and `supported_schemas` `["fusion_mcp_tools_v1"]`, `list_tools` and `call_tool` round-trip, `ping` returns `pong`, `execute_code` as a socket type is `not_supported`, a 401 stops retry, and the pairing secret is not logged.

Stop here if the integration service is out of scope for that session.

### Phase 4 — Integration service

Other repo: `~/Professional/tymeline/microservice/tymeline-integration-srv`. Section 8. Do this after phase 3. Keep the connect URL and the auth headers.

Done when:

- Connecting the Fusion 360 Desktop tile returns a `TYM1.` key once, and Mongo stores only the secret hash.
- The daemon opened with that key is on `wss://…/api/v1/integrations/plugin/connect` with `Authorization: Bearer` and `X-Tymeline-Token-Id`.
- Hello `cad_kind: "fusion360"` is stored on a Fusion device row, and the tile shows connected.
- `ExecutePluginCommand` `list_tools` returns the catalog.
- `ExecutePluginCommand` `call_tool` with `get_scene_info` returns the open design.
- A Fusion session is offered `list_tools` and `call_tool`.

---

## 11. Done when

- Hello on a stub gateway shows `cad_kind` `fusion360` and `supported_schemas` `["fusion_mcp_tools_v1"]`.
- `list_tools` returns the MCP tools and omits `execute_code` and `delete_all`.
- `call_tool` for one real tool name sends one add-in command.
- `call_tool` for `execute_code` or `delete_all` does not touch Fusion.
- The WebSocket stays closed while port 9876 does not answer.
- A 401 stops the retry loop, and the pairing secret is not logged.
- `apply_plan` is not dispatched.
- The integration service returns a `TYM1.` key from connect, stores only the secret hash, and accepts that key on `/api/v1/integrations/plugin/connect`.
- A Fusion hello on that hub is stored as Fusion, and a read goes out as `call_tool` `get_scene_info`.

---

## 12. Out of this plan

Leave these for a later plan. Do not implement them while doing sections 6–10.

- One undo group, one Ctrl+Z for several features, `apply_plan`
- `fusion_ops_v1`, parametric cylinder and sphere, `new_document`, `remove_object`, sketch-by-name
- Millimetre conversion
- Dev HTTP on `127.0.0.1:41829`
- Phase 1 spikes and live Ctrl+Z checks in `~/Professional/tymeline/fusion360/PLAN.md`
- Editing `autodesk-fusion-mcp/`
