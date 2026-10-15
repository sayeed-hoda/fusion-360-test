Morning Update (14-Oct-26 | Wed):

Todo:

  - Make the Tymeline pairing folder follow the OS: Windows AppData, macOS Application Support, Linux ~/.config. Plan is in `docs/PAIRING-PATH.md`.

  - Toolbar button is in the add-in: Design → Tymeline → Pair with Tymeline. It still has to be checked on Windows or macOS.

  - Add the Fusion 360 Desktop support in the integration service.

  - Update the frontend to support the Fusion 360 add-on flow.

  - Forward `list_tools` and `call_tool` on that live socket. A read of the open design is `call_tool` with `get_scene_info`.

  - Test Fusion 360 with the REPL and the overall plugin flow.

Blocker:

  - Fusion is not installed on this Linux machine, so the toolbar dialog has to be checked on Windows or macOS.
