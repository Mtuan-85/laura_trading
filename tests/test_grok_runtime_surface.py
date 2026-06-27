from __future__ import annotations

import importlib
import sys


def test_grok_package_runtime_surface_excludes_legacy_image_picker() -> None:
    sys.modules.pop("engines.grok", None)
    sys.modules.pop("engines.grok.claude_picker", None)

    grok = importlib.import_module("engines.grok")

    assert grok.__all__ == ["GrokConnection", "GrokVideoEngine"]
    assert not hasattr(grok, "GrokImageEngine")
    assert "engines.grok.claude_picker" not in sys.modules
