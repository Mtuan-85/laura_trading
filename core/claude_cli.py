"""Cross-platform helpers for spawning the `claude` CLI as a subprocess.

Handles the 6 Windows-specific gotchas documented in
`D:\\CLAUDE\\.claude\\skills\\claude-cli-subprocess\\SKILL.md`:

1. `WinError 2` — resolves `claude.cmd` via `shutil.which` (CreateProcessW
   does not honour PATHEXT for bare names).
2. Hangs on permission prompts — `--dangerously-skip-permissions`.
3. Cmd window flash from GUI — `CREATE_NO_WINDOW` + `STARTUPINFO/SW_HIDE`.
4. Wrong account — pops `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`.
5. UTF-8 mojibake — `encoding="utf-8"`, `errors="replace"`,
   `PYTHONIOENCODING=utf-8`.
6. 8191-char limit — prompt is piped via stdin.
"""

from __future__ import annotations

import os
import shutil
import subprocess

PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def find_claude_exe() -> str | None:
    """Locate the Claude CLI binary. On Windows it is usually `claude.cmd`."""
    for name in ("claude", "claude.exe", "claude.cmd"):
        path = shutil.which(name)
        if path:
            return path
    return None


def hidden_subprocess_kwargs() -> dict:
    """Popen kwargs that prevent the cmd.exe window from flashing on Windows."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def claude_env() -> dict:
    """Build an env that forces the user's logged-in session + UTF-8 output."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_claude(
    prompt: str,
    *,
    timeout: int,
    cwd: str | None = None,
    skip_permissions: bool = True,
) -> tuple[int, str, str]:
    """Spawn `claude --print`, push prompt via stdin, return (rc, stdout, stderr).

    Raises FileNotFoundError if the binary is not on PATH.
    Raises subprocess.TimeoutExpired if the call exceeds `timeout` seconds.
    """
    exe = find_claude_exe()
    if exe is None:
        raise FileNotFoundError(
            "Claude CLI not in PATH (tried claude/claude.exe/claude.cmd)"
        )

    cmd = [exe, "--print"]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=claude_env(),
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **hidden_subprocess_kwargs(),
    )
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    return proc.returncode, stdout, stderr
