"""Phase 2: pairing, device id, and fusion-agent serve. No socket to port 9876."""

from __future__ import annotations

import base64
import json
import os
import signal
import sys
from pathlib import Path, PosixPath

import pytest

from fusion_agent.cli import (
    _pid_alive,
    _remember_cli_executable,
    _write_status,
    cmd_pair,
    cmd_serve,
    cmd_status,
    cmd_stop,
    cmd_unpair,
    open_serve,
)
from fusion_agent.transport import device_id, pairing, paths


def _token(
    url: str, secret: str = "super-secret-value", token_id: str = "tok_1"
) -> str:
    raw = json.dumps(
        {"url": url, "token_id": token_id, "secret": secret, "display": "desk"}
    ).encode()
    payload = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return "TYM1." + payload


def test_repl_records_the_resolved_cli_for_the_fusion_addin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    program = tmp_path / "fusion-agent.exe"
    program.write_bytes(b"launcher")
    hint = tmp_path / ".tymeline-fusion-agent"
    monkeypatch.setattr(sys, "argv", [str(program), "repl"])
    monkeypatch.setattr(paths, "launcher_hint_path", lambda: hint)
    _remember_cli_executable()
    assert hint.read_text(encoding="utf-8") == str(program.resolve())


@pytest.fixture
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pairing_file = tmp_path / "pairing.json"
    pid_file = tmp_path / "agent.pid"
    status_file = tmp_path / "status.json"
    monkeypatch.setattr(pairing, "pairing_path", lambda: pairing_file)
    monkeypatch.setattr("fusion_agent.transport.paths.pid_path", lambda: pid_file)
    monkeypatch.setattr("fusion_agent.cli.paths.pid_path", lambda: pid_file)
    monkeypatch.setattr("fusion_agent.cli.paths.status_path", lambda: status_file)
    monkeypatch.delenv("FUSION_AGENT_GATEWAY", raising=False)
    monkeypatch.delenv("FUSION_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("FUSION_AGENT_TOKEN_ID", raising=False)
    return pairing_file, pid_file


def test_pair_writes_token_and_does_not_print_the_secret(isolated_config, capsys):
    pairing_file, _pid = isolated_config
    secret = "super-secret-value"
    code = cmd_pair(_token("ws://127.0.0.1:53117", secret=secret))
    captured = capsys.readouterr()
    assert code == 0
    assert "tok_1" in captured.out
    assert "127.0.0.1" in captured.out
    assert secret not in captured.out
    assert secret not in captured.err
    stored = json.loads(pairing_file.read_text(encoding="utf-8"))
    assert stored["secret"] == secret
    assert stored["token_id"] == "tok_1"
    if os.name != "nt":
        assert pairing_file.stat().st_mode & 0o777 == 0o600


def test_pair_refuses_an_untrusted_host(isolated_config, capsys):
    pairing_file, _pid = isolated_config
    code = cmd_pair(_token("ws://evil.example/connect"))
    captured = capsys.readouterr()
    assert code == 2
    assert "Refusing to pair" in captured.err
    assert "super-secret-value" not in captured.err
    assert not pairing_file.exists()


def test_unpair_deletes_the_file(isolated_config, capsys):
    pairing_file, _pid = isolated_config
    assert cmd_pair(_token("wss://gateway.tymeline.app")) == 0
    capsys.readouterr()
    assert pairing_file.is_file()
    assert cmd_unpair() == 0
    assert "unpaired" in capsys.readouterr().out
    assert not pairing_file.exists()


def test_serve_exits_with_a_message_when_not_paired(isolated_config, capsys):
    code = cmd_serve()
    captured = capsys.readouterr()
    assert code == 1
    assert "fusion-agent pair" in captured.out
    assert "super-secret-value" not in captured.out


def test_serve_starts_from_a_trusted_env_pair(isolated_config, monkeypatch, capsys):
    _pairing_file, pid_file = isolated_config
    started: dict = {}

    class FakeClient:
        def __init__(self, url, secret, token_id="", service=None, **_kwargs):
            started["url"] = url
            started["secret"] = secret
            started["token_id"] = token_id
            started["service"] = service

        def start(self):
            started["started"] = True

        def stop(self):
            started["stopped"] = True

    monkeypatch.setattr("fusion_agent.transport.ws_client.WsClient", FakeClient)
    monkeypatch.setenv("FUSION_AGENT_GATEWAY", "ws://127.0.0.1:53117")
    monkeypatch.setenv("FUSION_AGENT_TOKEN", "super-secret-value")
    monkeypatch.setenv("FUSION_AGENT_TOKEN_ID", "tid")
    client, code = open_serve()
    captured = capsys.readouterr()
    try:
        assert code == 0
        assert client is started.get("service") or started["started"] is True
        assert started["started"] is True
        assert started["url"].endswith("/api/v1/integrations/plugin/connect")
        assert started["secret"] == "super-secret-value"
        assert "super-secret-value" not in captured.out
        assert captured.out.isascii()
        assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())
    finally:
        if client is not None:
            client.stop()
        if pid_file.exists():
            pid_file.unlink()


def test_second_serve_does_not_start(isolated_config, monkeypatch, capsys):
    _pairing_file, pid_file = isolated_config
    pid_file.write_text(f"{os.getppid()}\n", encoding="utf-8")
    monkeypatch.setenv("FUSION_AGENT_GATEWAY", "ws://127.0.0.1:53117")
    monkeypatch.setenv("FUSION_AGENT_TOKEN", "super-secret-value")

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("second process must not construct a client")

        def start(self):
            raise AssertionError("second process must not start")

    monkeypatch.setattr("fusion_agent.transport.ws_client.WsClient", FakeClient)
    client, code = open_serve()
    captured = capsys.readouterr()
    assert client is None
    assert code == 1
    assert "Already running" in captured.out


def test_status_reports_live_connection_without_secret(isolated_config, capsys):
    _pairing_file, pid_file = isolated_config
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")

    class Client:
        connected = True
        fusion_connected = True
        gave_up = False
        gateway_url = "wss://gateway.tymeline.app/connect"

    _write_status(Client())
    assert cmd_status() == 0
    output = capsys.readouterr().out
    assert "Tymeline is connected" in output
    assert "Fusion is connected" in output
    assert "secret" not in output


def test_status_treats_a_windows_missing_process_as_disconnected(
    isolated_config, monkeypatch, capsys
):
    """WinError 87 is how Windows answers os.kill(pid, 0) for a dead pid."""
    _pairing_file, pid_file = isolated_config
    pid_file.write_text("4242\n", encoding="utf-8")

    def fake_kill(pid: int, sig: int) -> None:
        assert (pid, sig) == (4242, 0)
        raise OSError(22, "The parameter is incorrect", None, 87)

    monkeypatch.setattr("fusion_agent.cli.os.kill", fake_kill)
    assert _pid_alive(0) is False
    assert _pid_alive(4242) is False
    assert cmd_status() == 0
    assert "disconnected" in capsys.readouterr().out


def test_pid_alive_counts_an_unsignalable_process_as_running(monkeypatch):
    def fake_kill(pid: int, sig: int) -> None:
        assert (pid, sig) == (4242, 0)
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr("fusion_agent.cli.os.kill", fake_kill)
    assert _pid_alive(4242) is True


def test_status_reports_reconnecting_and_disconnected(isolated_config, capsys):
    _pairing_file, pid_file = isolated_config
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")

    class Client:
        connected = False
        fusion_connected = False
        gave_up = False
        gateway_url = "wss://gateway.tymeline.app/connect"

    _write_status(Client())
    assert cmd_status() == 0
    assert "reconnecting" in capsys.readouterr().out
    pid_file.unlink()
    assert cmd_status() == 0
    assert "disconnected" in capsys.readouterr().out


def _pin_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Point Path.home at a temp directory and clear config env vars.

    pathlib 3.13+ builds a WindowsPath when os.name is ``nt``. Those objects
    cannot be joined on Linux, so the tests keep the host path class while
    config_root still reads os.name.
    """
    home.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        monkeypatch.setattr(paths, "Path", PosixPath)
    monkeypatch.setattr(paths.Path, "home", lambda: home)
    monkeypatch.delenv("FUSION_AGENT_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)


def test_config_root_on_windows_uses_appdata(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    appdata = tmp_path / "Roaming"
    _pin_home(monkeypatch, home)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata))
    assert paths.config_root() == appdata / "tymeline-fusion360"


def test_config_root_on_windows_falls_back_when_appdata_is_missing(
    monkeypatch, tmp_path: Path
):
    home = tmp_path / "home"
    _pin_home(monkeypatch, home)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(sys, "platform", "win32")
    assert paths.config_root() == home / "AppData" / "Roaming" / "tymeline-fusion360"


def test_config_root_on_macos_uses_application_support(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    _pin_home(monkeypatch, home)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert (
        paths.config_root()
        == home / "Library" / "Application Support" / "tymeline-fusion360"
    )


def test_config_root_on_linux_uses_xdg_or_dot_config(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    _pin_home(monkeypatch, home)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "linux")
    legacy = home / ".config" / "tymeline-fusion360"
    legacy.mkdir(parents=True)
    (legacy / "pairing.json").write_text("old\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert paths.config_root() == xdg / "tymeline-fusion360"
    assert not (xdg / "tymeline-fusion360" / "pairing.json").exists()
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert paths.config_root() == home / ".config" / "tymeline-fusion360"


def test_config_root_override_wins_over_the_os_folder(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    appdata = tmp_path / "Roaming"
    override = tmp_path / "override"
    _pin_home(monkeypatch, home)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("FUSION_AGENT_CONFIG_DIR", str(override))
    assert paths.config_root() == override


def test_legacy_pairing_is_copied_when_the_new_file_is_missing(
    monkeypatch, tmp_path: Path
):
    home = tmp_path / "home"
    appdata = tmp_path / "Roaming"
    _pin_home(monkeypatch, home)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata))
    legacy = home / ".config" / "tymeline-fusion360"
    legacy.mkdir(parents=True)
    (legacy / "pairing.json").write_text('{"secret":"kept"}\n', encoding="utf-8")
    (legacy / "device_id").write_text("dev-1\n", encoding="utf-8")
    (legacy / "agent.pid").write_text("99\n", encoding="utf-8")

    root = paths.config_root()
    assert (root / "pairing.json").read_text(encoding="utf-8") == '{"secret":"kept"}\n'
    assert (root / "device_id").read_text(encoding="utf-8") == "dev-1\n"
    assert not (root / "agent.pid").exists()
    assert (legacy / "pairing.json").is_file()
    assert (legacy / "device_id").is_file()
    assert (legacy / "agent.pid").is_file()

    (legacy / "pairing.json").write_text("changed\n", encoding="utf-8")
    paths.config_root()
    assert (root / "pairing.json").read_text(encoding="utf-8") == '{"secret":"kept"}\n'


def test_legacy_pairing_is_not_copied_when_the_override_is_set(
    monkeypatch, tmp_path: Path
):
    home = tmp_path / "home"
    appdata = tmp_path / "Roaming"
    override = tmp_path / "override"
    _pin_home(monkeypatch, home)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata))
    legacy = home / ".config" / "tymeline-fusion360"
    legacy.mkdir(parents=True)
    (legacy / "pairing.json").write_text('{"secret":"kept"}\n', encoding="utf-8")
    monkeypatch.setenv("FUSION_AGENT_CONFIG_DIR", str(override))

    assert paths.config_root() == override
    assert not (override / "pairing.json").exists()
    assert not (appdata / "tymeline-fusion360" / "pairing.json").exists()
    assert (legacy / "pairing.json").is_file()


def test_stop_reports_when_nothing_is_running(isolated_config, capsys):
    code = cmd_stop()
    assert code == 0
    assert "Not running" in capsys.readouterr().out


def test_stop_treats_a_windows_missing_process_as_not_running(
    isolated_config, monkeypatch, capsys
):
    """Pair calls stop first. A stale pid must not abort that step on Windows."""
    _pairing_file, pid_file = isolated_config
    pid_file.write_text("4242\n", encoding="utf-8")

    def fake_kill(pid: int, sig: int) -> None:
        raise OSError(22, "The parameter is incorrect", None, 87)

    monkeypatch.setattr("fusion_agent.cli.os.kill", fake_kill)
    assert cmd_stop() == 0
    assert "Not running" in capsys.readouterr().out
    assert not pid_file.exists()


def test_stop_signals_a_running_agent(isolated_config, monkeypatch):
    _pairing_file, pid_file = isolated_config
    pid_file.write_text("4242\n", encoding="utf-8")
    killed: list[int] = []

    def alive(pid: int) -> bool:
        return pid not in killed

    def fake_kill(pid: int, sig: int) -> None:
        assert sig == signal.SIGTERM
        killed.append(pid)

    monkeypatch.setattr("fusion_agent.cli._pid_alive", alive)
    monkeypatch.setattr("fusion_agent.cli.os.kill", fake_kill)
    assert cmd_stop() == 0
    assert killed == [4242]


def test_device_id_is_reused_and_survives_a_missing_directory(tmp_path: Path):
    dest = tmp_path / "device_id"
    first = device_id.get_or_create(dest)
    second = device_id.get_or_create(dest)
    assert first == second
    assert dest.read_text(encoding="utf-8").strip() == first

    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    ident = device_id.get_or_create(blocked / "device_id")
    assert ident
    assert ident != first
