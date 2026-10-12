"""Phase 2: pairing, device id, and fusion-agent serve. No socket to port 9876."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from fusion_agent.cli import cmd_pair, cmd_serve, cmd_unpair, open_serve
from fusion_agent.transport import device_id, pairing


def _token(
    url: str, secret: str = "super-secret-value", token_id: str = "tok_1"
) -> str:
    raw = json.dumps(
        {"url": url, "token_id": token_id, "secret": secret, "display": "desk"}
    ).encode()
    payload = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return "TYM1." + payload


@pytest.fixture
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pairing_file = tmp_path / "pairing.json"
    pid_file = tmp_path / "agent.pid"
    monkeypatch.setattr(pairing, "pairing_path", lambda: pairing_file)
    monkeypatch.setattr("fusion_agent.transport.paths.pid_path", lambda: pid_file)
    monkeypatch.setattr("fusion_agent.cli.paths.pid_path", lambda: pid_file)
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
