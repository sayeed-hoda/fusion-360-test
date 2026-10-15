"""The Fusion toolbar starts fusion-agent. It does not choose the pairing folder."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LAUNCH_PATH = REPO / "addon" / "server" / "agent_launch.py"
BUTTON_PATH = REPO / "addon" / "server" / "pairing_button.py"
ADDIN_PATH = REPO / "addon" / "TymelineFusion360.py"


def _load():
    spec = importlib.util.spec_from_file_location("agent_launch", LAUNCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def launch():
    return _load()


def _bin(tmp_path: Path) -> Path:
    path = tmp_path / "fusion-agent"
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_pair_stops_the_old_connection_and_starts_serve(
    launch, monkeypatch, tmp_path: Path
):
    program = _bin(tmp_path)
    monkeypatch.setenv("FUSION_AGENT_BIN", str(program))
    monkeypatch.setenv("PYTHONHOME", "/fusion/python")
    calls: list[list[str]] = []
    envs: list[dict] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        envs.append(kwargs["env"])
        if argv[-1] == "stop":
            return subprocess.CompletedProcess(argv, 0, stdout="[Agent] Not running.\n")
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="paired token_id=tok_1 url=wss://gateway.tymeline.app\n",
            stderr="TYM1.secret-value must not leak\n",
        )

    spawned: dict = {}

    def fake_popen(argv, **kwargs):
        spawned["argv"] = list(argv)
        spawned["kwargs"] = kwargs
        return object()

    log_file = tmp_path / "agent.log"
    message = launch.pair_and_start(
        "  TYM1.secret-value  ",
        run=fake_run,
        popen=fake_popen,
        log_file=log_file,
    )
    assert calls[0][-2:] == ["pair", "TYM1.secret-value"]
    assert calls[1][-1] == "stop"
    assert spawned["argv"] == [str(program), "serve"]
    assert "secret-value" not in message
    assert "tok_1" in message
    assert "starting" in message
    assert "PYTHONHOME" not in envs[0]
    assert spawned["kwargs"]["start_new_session"] is True
    assert log_file.parent.is_dir()


def test_a_rejected_key_does_not_start_serve(launch, monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FUSION_AGENT_BIN", str(_bin(tmp_path)))
    calls: list[str] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv[-1] if argv[-1] != "TYM1.secret-value" else argv[-2])
        return subprocess.CompletedProcess(
            argv, 2, stdout="", stderr="Refusing to pair TYM1.secret-value"
        )

    def fake_popen(*_args, **_kwargs):
        raise AssertionError("serve must not start")

    with pytest.raises(launch.AgentLaunchError, match="Refusing to pair") as raised:
        launch.pair_and_start("TYM1.secret-value", run=fake_run, popen=fake_popen)
    assert calls == ["pair"]
    assert "secret-value" not in str(raised.value)


def test_serve_does_not_start_when_the_old_connection_stays_up(
    launch, monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("FUSION_AGENT_BIN", str(_bin(tmp_path)))

    def fake_run(argv, **_kwargs):
        if argv[-1] == "stop":
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="[Agent] Still running (pid 9)."
            )
        return subprocess.CompletedProcess(argv, 0, stdout="paired token_id=tok_1\n")

    def fake_popen(*_args, **_kwargs):
        raise AssertionError("serve must not start")

    with pytest.raises(launch.AgentLaunchError, match="Still running"):
        launch.pair_and_start("TYM1.abc", run=fake_run, popen=fake_popen)


def test_unpair_forgets_the_key_and_stops(launch, monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FUSION_AGENT_BIN", str(_bin(tmp_path)))
    calls: list[str] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv[-1])
        stdout = "unpaired\n" if argv[-1] == "unpair" else "[Agent] Stopped (pid 4).\n"
        return subprocess.CompletedProcess(argv, 0, stdout=stdout)

    assert launch.unpair(run=fake_run) == "Tymeline pairing removed."
    assert calls == ["unpair", "stop"]


def test_unpair_when_nothing_was_stored(launch, monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FUSION_AGENT_BIN", str(_bin(tmp_path)))

    def fake_run(argv, **_kwargs):
        stdout = "not paired\n" if argv[-1] == "unpair" else "[Agent] Not running.\n"
        return subprocess.CompletedProcess(argv, 0, stdout=stdout)

    assert launch.unpair(run=fake_run) == "Tymeline was not paired."


def test_connection_status_uses_the_agent_command(
    launch, monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("FUSION_AGENT_BIN", str(_bin(tmp_path)))
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv, 0, stdout="Tymeline is connected. Fusion is connected.\n"
        )

    message = launch.connection_status(run=fake_run)
    assert message == "Tymeline is connected. Fusion is connected."
    assert calls[0][-1] == "status"


def test_start_connection_launches_serve_with_saved_pairing(
    launch, monkeypatch, tmp_path: Path
):
    program = _bin(tmp_path)
    monkeypatch.setenv("FUSION_AGENT_BIN", str(program))
    spawned: dict = {}

    def fake_popen(argv, **kwargs):
        spawned["argv"] = list(argv)
        spawned["kwargs"] = kwargs
        return object()

    message = launch.start_connection(popen=fake_popen, log_file=tmp_path / "agent.log")
    assert message == "Tymeline connection is starting."
    assert spawned["argv"] == [str(program), "serve"]
    assert spawned["kwargs"]["start_new_session"] is True


def test_stop_connection_keeps_pairing_and_stops_the_daemon(
    launch, monkeypatch, tmp_path: Path
):
    program = _bin(tmp_path)
    monkeypatch.setenv("FUSION_AGENT_BIN", str(program))
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv, 0, stdout="[Agent] Stopped (pid 42).\n"
        )

    message = launch.stop_connection(run=fake_run)
    assert message == "[Agent] Stopped (pid 42)."
    assert calls == [[str(program), "stop"]]


def test_missing_program_is_reported(launch, monkeypatch, tmp_path: Path):
    monkeypatch.delenv("FUSION_AGENT_BIN", raising=False)
    monkeypatch.delenv("FUSION_AGENT_PROJECT", raising=False)
    monkeypatch.setattr(launch.shutil, "which", lambda _name: None)
    with pytest.raises(launch.AgentLaunchError, match="FUSION_AGENT_BIN"):
        launch.pair_and_start("TYM1.abc", start=tmp_path / "outside")


def test_project_venv_is_used_when_the_program_is_not_on_path(
    launch, monkeypatch, tmp_path: Path
):
    monkeypatch.delenv("FUSION_AGENT_BIN", raising=False)
    monkeypatch.delenv("FUSION_AGENT_PROJECT", raising=False)
    monkeypatch.setattr(launch.shutil, "which", lambda _name: None)
    root = tmp_path / "repo"
    (root / "src" / "fusion_agent").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "src" / "fusion_agent" / "cli.py").write_text("#\n", encoding="utf-8")
    venv_bin = root / ".venv" / "bin" / "fusion-agent"
    venv_bin.parent.mkdir(parents=True)
    venv_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    venv_bin.chmod(0o755)
    found = launch.agent_command(start=root / "addon" / "server" / "agent_launch.py")
    assert found == [str(venv_bin)]


def test_windows_spawn_hides_the_command_prompt(launch):
    flags = launch.spawn_kwargs("nt")["creationflags"]
    assert flags & launch._CREATE_NO_WINDOW
    assert flags & launch._CREATE_NEW_PROCESS_GROUP
    assert not (flags & 0x00000008)
    assert launch.spawn_kwargs("posix") == {"start_new_session": True}


def test_the_add_in_does_not_choose_the_pairing_folder():
    launch_text = LAUNCH_PATH.read_text(encoding="utf-8")
    button_text = BUTTON_PATH.read_text(encoding="utf-8")
    addin_text = ADDIN_PATH.read_text(encoding="utf-8")
    for text in (launch_text, button_text):
        assert "config_root" not in text
        assert "APPDATA" not in text
        assert "pairing.json" not in text
    assert "pair_and_start" in button_text
    assert "connection_status" in button_text
    assert "Tymeline Status" in button_text
    assert "addButtonDefinition" in button_text
    assert "Paste the TYM1 pairing key" in button_text
    assert "_LOGO_FOLDER" in button_text
    assert "_install_pairing_button" in addin_text
    assert "_start_tymeline_connection()" in addin_text
    assert "_stop_tymeline_connection()" in addin_text
    assert "start_connection" in addin_text
    assert "stop_connection" in addin_text
    assert "_start_repl_announce" not in addin_text


def test_toolbar_logo_matches_fusion_icon_sizes():
    """Fusion picks the toolbar icon by filename and pixel size."""
    import struct

    folder = REPO / "addon" / "resources" / "tymeline"
    expected = {
        "16x16.png": (16, 16),
        "16x16@2x.png": (32, 32),
        "32x32.png": (32, 32),
        "32x32@2x.png": (64, 64),
        "64x64.png": (64, 64),
    }
    for name, size in expected.items():
        data = (folder / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        assert data[12:16] == b"IHDR", name
        assert struct.unpack(">II", data[16:24]) == size, name
        assert data[25] == 6, name
