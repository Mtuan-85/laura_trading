"""Grok adapter — implements ImageEngine, VideoEngine, EngineConnection.

Public surface:
    GrokConnection   — CDP attach + tab management (EngineConnection)
    GrokImageEngine  — generate stills via grok.com/imagine (ImageEngine)
    GrokVideoEngine  — image/text → video via grok.com/imagine (VideoEngine)

Internal modules (selectors, actions, flows, runner) are stable refactors of
the proven grok_automation reference. DOM selectors live in selectors.py only
— never reach in from outside the package.
"""

from engines.grok.browser import GrokConnection
from engines.grok.engine import GrokImageEngine, GrokVideoEngine

__all__ = ["GrokConnection", "GrokImageEngine", "GrokVideoEngine"]
