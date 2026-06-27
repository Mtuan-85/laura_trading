"""Grok adapter — implements VideoEngine and EngineConnection.

Public surface:
    GrokConnection   — CDP attach + tab management (EngineConnection)
    GrokVideoEngine  — image/text → video via grok.com/imagine (VideoEngine)

Internal modules (selectors, actions, flows, runner) are stable refactors of
the proven grok_automation reference. DOM selectors live in selectors.py only
— never reach in from outside the package.
"""

from engines.grok.browser import GrokConnection
from engines.grok.engine import GrokVideoEngine

__all__ = ["GrokConnection", "GrokVideoEngine"]
