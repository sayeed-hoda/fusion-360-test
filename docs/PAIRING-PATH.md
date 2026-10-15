# Pairing folder — Windows, macOS, and Linux

**Date:** 2026-10-14.
**Status:** implemented in `config_root()`. Do not start this from `docs/PLAN.md` phases.
**Repo:** `fusion360-mcp-server/`.

The Tymeline key is stored in `pairing.json`. Today every machine writes that file under `~/.config/tymeline-fusion360/`. That folder is correct on Linux. On Windows and macOS the file belongs in the normal per-user settings folder for that system.

Fusion desktop is Windows and macOS. Tests and the daemon still run on Linux. One function picks the folder. `pairing.json`, `device_id`, and `agent.pid` stay inside it.

The key format does not change. The trust check does not change. The WebSocket headers do not change.

---

## 1. Where the file goes

| System | Folder |
|---|---|
| Windows | `%APPDATA%\tymeline-fusion360\` |
| macOS | `~/Library/Application Support/tymeline-fusion360/` |
| Linux | `$XDG_CONFIG_HOME/tymeline-fusion360/` or `~/.config/tymeline-fusion360/` |

`%APPDATA%` on a normal Windows account is `C:\Users\<name>\AppData\Roaming`.

The file name stays `pairing.json` on every system. The same folder also holds `device_id` and `agent.pid`.

Do not put these files in the Fusion add-in install folder. Reinstalling the add-in would delete the key, and `fusion-agent` would have to search for that folder.

---

## 2. The one function that picks the folder

`config_root()` in `src/fusion_agent/transport/paths.py` is the only place that chooses the directory. Today it is:

```python
def config_root() -> Path:
    return Path.home() / ".config" / CONFIG_DIRNAME
```

Replace that body. `pairing_path()`, `device_id_path()`, and `pid_path()` stay as they are. `CONFIG_DIRNAME` stays `tymeline-fusion360`.

Rules, in this order:

1. If `FUSION_AGENT_CONFIG_DIR` is set, use that path as the whole folder. This is for tests. A normal install does not set it.
2. Windows (`os.name == "nt"`): use `APPDATA`. If `APPDATA` is missing, use `Path.home() / "AppData" / "Roaming"`.
3. macOS (`sys.platform == "darwin"`): use `Path.home() / "Library" / "Application Support"`.
4. Anything else: use `XDG_CONFIG_HOME` when it is set, otherwise `Path.home() / ".config"`.

Then append `tymeline-fusion360`, except when rule 1 already gave the whole folder.

`save_pairing`, `load_pairing`, and `get_or_create` already call these helpers. They do not grow their own path logic.

The Design workspace has a Tymeline panel. Pair with Tymeline asks for the `TYM1.` key, then runs `fusion-agent pair` and `fusion-agent serve`. Unpair runs `fusion-agent unpair`. The add-in does not build a second path. Both programs then see the same file.

---

## 3. A key that was already saved

On Linux the new folder and the old folder are the same path, so nothing moves.

On Windows and macOS the current code may already have written `Path.home() / ".config" / "tymeline-fusion360"`. On first read, if the new folder has no `pairing.json` and that old file exists, copy `pairing.json` and `device_id` into the new folder. Leave the old files in place. Do not copy `agent.pid`.

Skip the copy when `FUSION_AGENT_CONFIG_DIR` is set. A test folder must not pull in a real key from the home directory.

---

## 4. Who can read the file

`save_pairing` in `src/fusion_agent/transport/pairing.py` writes the JSON and then calls `os.chmod(dest, 0o600)`.

On macOS and Linux that mode means only the owner can read the secret. Keep that call.

On Windows `os.chmod(0o600)` does not mean the same thing. The privacy boundary there is the user profile: `%APPDATA%` is already private to that account. Do not describe the Windows file as mode `0600`.

`tests/test_fusion_agent_cli.py` asserts `pairing_file.stat().st_mode & 0o777 == 0o600`. Keep that assertion on POSIX. Do not require that mode when `os.name == "nt"`.

---

## 5. Tests

Add them next to the existing pairing tests. They must not write the real home folder. Set `FUSION_AGENT_CONFIG_DIR` or monkeypatch `Path.home` and the environment.

| Test | Proves |
|---|---|
| Windows | `os.name == "nt"` and `APPDATA` set returns `%APPDATA%\tymeline-fusion360`. |
| Windows fallback | `APPDATA` missing returns `~\AppData\Roaming\tymeline-fusion360`. |
| macOS | `sys.platform == "darwin"` returns `~/Library/Application Support/tymeline-fusion360`. |
| Linux | `XDG_CONFIG_HOME` set uses it. Unset uses `~/.config/tymeline-fusion360`. |
| Override | `FUSION_AGENT_CONFIG_DIR` wins over the OS folder. |
| Legacy copy | Old `~/.config/tymeline-fusion360/pairing.json` is copied when the new file is missing. It is not copied when the override is set. |

The tests in `tests/test_fusion_agent_cli.py` already replace `pairing_path` with a temp file. Leave that fixture. Those tests stay green without a real Fusion install.

No test opens a socket to port 9876. No test imports `adsk`.

---

## 6. Docs to update when this is implemented

After `config_root()` is changed, replace the `~/.config/tymeline-fusion360/pairing.json` sentences in `docs/PLAN.md` and `docs/HANDOFF.md` with the three folders in section 1. Update the docstring in `src/fusion_agent/transport/device_id.py` the same way.

---

## 7. Done when

- `config_root()` returns the Windows, macOS, and Linux folders in section 1.
- `fusion-agent pair` writes `pairing.json` in that folder.
- `fusion-agent serve` reads that same file.
- A key left in `~/.config/tymeline-fusion360/` on Windows or macOS is copied once into the new folder.
- The POSIX tests still require mode `0600`. The Windows path does not.
- The existing pair, unpair, and serve tests still pass.
