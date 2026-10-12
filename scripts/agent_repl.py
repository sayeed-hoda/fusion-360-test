#!/usr/bin/env python3
"""Chat with a GLM model that drives the local Fusion add-in.

Fusion must already be listening on 127.0.0.1:9876. This process sends the
conversation to an OpenAI-compatible /v1/chat/completions endpoint and applies
tool calls locally. The model never opens a socket to Fusion.

The API key is read from the environment. It is not stored in this repo.

    export GLM_API_KEY='...'
    export GLM_BASE_URL='https://example.modal.direct/v1'   # optional
    uv run python scripts/agent_repl.py
"""

# ruff: noqa: E402, I001
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fusion_agent.repl import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
