"""Design-workspace buttons that paste a TYM1 key and start the connection.

The buttons call ``fusion-agent``. They do not choose the pairing folder.
"""

from __future__ import annotations

import traceback
from pathlib import Path

import adsk.core

from . import get_logger
from .agent_launch import AgentLaunchError, connection_status, pair_and_start, unpair

PAIR_CMD_ID = "tymeline_fusion_pair"
UNPAIR_CMD_ID = "tymeline_fusion_unpair"
STATUS_CMD_ID = "tymeline_fusion_status"
PANEL_ID = "TymelineFusionPanel"
WORKSPACE_ID = "FusionSolidEnvironment"
_ADDINS_PANEL = "SolidScriptsAddinsPanel"
# Fusion reads 16x16, 32x32, and the @2x / 64x64 files from this folder.
# The path is absolute so a symlinked add-in still finds the icons.
_LOGO_FOLDER = str(Path(__file__).resolve().parents[1] / "resources" / "tymeline")

_log = get_logger("pairing")
_handlers: list = []


def install(ui: adsk.core.UserInterface) -> None:
    """Add Pair and Unpair to the Design workspace Tymeline panel."""
    _remove_controls(ui)
    if not Path(_LOGO_FOLDER).is_dir():
        _log.warning("Tymeline toolbar icon folder is missing: %s", _LOGO_FOLDER)
    pair_def = ui.commandDefinitions.addButtonDefinition(
        PAIR_CMD_ID,
        "Pair with Tymeline",
        "Paste your Tymeline API key (it starts with TYM1.). "
        "This is the key that opens the WebSocket to Tymeline.",
        _LOGO_FOLDER,
    )
    unpair_def = ui.commandDefinitions.addButtonDefinition(
        UNPAIR_CMD_ID,
        "Unpair Tymeline",
        "Forget the Tymeline pairing key and disconnect.",
        _LOGO_FOLDER,
    )
    status_def = ui.commandDefinitions.addButtonDefinition(
        STATUS_CMD_ID,
        "Tymeline Status",
        "Show whether Fusion is connected or reconnecting to Tymeline.",
        _LOGO_FOLDER,
    )
    pair_created = _PairCreated()
    pair_def.commandCreated.add(pair_created)
    _handlers.append(pair_created)
    unpair_created = _UnpairCreated()
    unpair_def.commandCreated.add(unpair_created)
    _handlers.append(unpair_created)
    status_created = _StatusCreated()
    status_def.commandCreated.add(status_created)
    _handlers.append(status_created)

    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    if workspace is None:
        raise RuntimeError("Design workspace is not available.")
    panel = _tymeline_panel(workspace)
    pair_control = panel.controls.addCommand(pair_def)
    pair_control.isPromoted = True
    try:
        pair_control.isPromotedByDefault = True
    except AttributeError:
        _log.info("This Fusion build does not promote the Pair button by default.")
    panel.controls.addCommand(unpair_def)
    panel.controls.addCommand(status_def)


def remove(ui: adsk.core.UserInterface | None) -> None:
    """Remove the buttons. Safe to call when they were never added."""
    if ui is not None:
        _remove_controls(ui)
    _handlers.clear()


def _tymeline_panel(workspace: adsk.core.Workspace) -> adsk.core.ToolbarPanel:
    existing = workspace.toolbarPanels.itemById(PANEL_ID)
    if existing is not None:
        return existing
    try:
        return workspace.toolbarPanels.add(PANEL_ID, "Tymeline", _ADDINS_PANEL, False)
    except RuntimeError:
        return workspace.toolbarPanels.add(PANEL_ID, "Tymeline", "", False)


def _remove_controls(ui: adsk.core.UserInterface) -> None:
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel = None
    if workspace is not None:
        panel = workspace.toolbarPanels.itemById(PANEL_ID)
    if panel is not None:
        for cmd_id in (PAIR_CMD_ID, UNPAIR_CMD_ID, STATUS_CMD_ID):
            control = panel.controls.itemById(cmd_id)
            if control is not None:
                control.deleteMe()
        panel.deleteMe()
    for cmd_id in (PAIR_CMD_ID, UNPAIR_CMD_ID, STATUS_CMD_ID):
        command = ui.commandDefinitions.itemById(cmd_id)
        if command is not None:
            command.deleteMe()


def _fit_dialog(cmd: adsk.core.Command, width: int, height: int) -> None:
    """Widen the click dialog so the pairing text is readable."""
    for name in ("setDialogInitialSize", "setDialogMinimumSize"):
        setter = getattr(cmd, name, None)
        if setter is None:
            continue
        try:
            setter(width, height)
        except Exception:
            _log.info("Fusion did not accept dialog size via %s.", name)


def _report(message: str, *, error: bool = False, show: bool = False) -> None:
    """Log every result; use a modal dialog only for errors or explicit status."""
    text = message.strip() or ("Pairing failed." if error else "Paired.")
    if error:
        _log.error("%s", text)
    else:
        _log.info("%s", text)
    app = adsk.core.Application.get()
    ui = app.userInterface if app is not None else None
    if ui is not None and (error or show):
        title = "Tymeline pairing failed" if error else "Tymeline"
        ui.messageBox(text, title)


class _PairCreated(adsk.core.CommandCreatedEventHandler):
    def __init__(self) -> None:
        super().__init__()

    def notify(self, args: adsk.core.CommandCreatedEventArgs) -> None:
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            _fit_dialog(cmd, 480, 180)
            cmd.commandInputs.addTextBoxCommandInput(
                "about",
                "",
                "Paste the Tymeline pairing key from your account. "
                "It starts with TYM1. and is the key that connects Fusion.",
                3,
                True,
            )
            cmd.commandInputs.addStringValueInput(
                "token",
                "Paste the TYM1 pairing key",
                "",
            )
            on_execute = _PairExecute()
            cmd.execute.add(on_execute)
            _handlers.append(on_execute)
            on_validate = _PairValidate()
            cmd.validateInputs.add(on_validate)
            _handlers.append(on_validate)
        except Exception:
            _report(traceback.format_exc(), error=True)


class _PairValidate(adsk.core.ValidateInputsEventHandler):
    def __init__(self) -> None:
        super().__init__()

    def notify(self, args: adsk.core.ValidateInputsEventArgs) -> None:
        event_args = adsk.core.ValidateInputsEventArgs.cast(args)
        field = event_args.inputs.itemById("token")
        token = ""
        if field is not None:
            cast = adsk.core.StringValueCommandInput.cast(field)
            if cast is not None:
                token = cast.value or ""
        event_args.areInputsValid = token.strip().startswith("TYM1.")


class _PairExecute(adsk.core.CommandEventHandler):
    def __init__(self) -> None:
        super().__init__()

    def notify(self, args: adsk.core.CommandEventArgs) -> None:
        try:
            cmd = adsk.core.CommandEventArgs.cast(args).command
            field = adsk.core.StringValueCommandInput.cast(
                cmd.commandInputs.itemById("token")
            )
            token = (field.value or "").strip() if field is not None else ""
            _report(pair_and_start(token))
        except AgentLaunchError as exc:
            _report(str(exc), error=True)
        except Exception:
            _report(traceback.format_exc(), error=True)


class _UnpairCreated(adsk.core.CommandCreatedEventHandler):
    def __init__(self) -> None:
        super().__init__()

    def notify(self, args: adsk.core.CommandCreatedEventArgs) -> None:
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            _fit_dialog(cmd, 420, 160)
            cmd.commandInputs.addTextBoxCommandInput(
                "confirm",
                "Unpair",
                "Remove the Tymeline pairing from this Fusion?",
                2,
                True,
            )
            on_execute = _UnpairExecute()
            cmd.execute.add(on_execute)
            _handlers.append(on_execute)
        except Exception:
            _report(traceback.format_exc(), error=True)


class _UnpairExecute(adsk.core.CommandEventHandler):
    def __init__(self) -> None:
        super().__init__()

    def notify(self, args: adsk.core.CommandEventArgs) -> None:
        del args
        try:
            _report(unpair())
        except AgentLaunchError as exc:
            _report(str(exc), error=True)
        except Exception:
            _report(traceback.format_exc(), error=True)


class _StatusCreated(adsk.core.CommandCreatedEventHandler):
    def __init__(self) -> None:
        super().__init__()

    def notify(self, args: adsk.core.CommandCreatedEventArgs) -> None:
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            on_execute = _StatusExecute()
            cmd.execute.add(on_execute)
            _handlers.append(on_execute)
        except Exception:
            _report(traceback.format_exc(), error=True)


class _StatusExecute(adsk.core.CommandEventHandler):
    def __init__(self) -> None:
        super().__init__()

    def notify(self, args: adsk.core.CommandEventArgs) -> None:
        del args
        try:
            _report(connection_status(), show=True)
        except AgentLaunchError as exc:
            _report(str(exc), error=True)
        except Exception:
            _report(traceback.format_exc(), error=True)
