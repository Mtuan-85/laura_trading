from __future__ import annotations

import os
import subprocess

import pytest

from core import claude_cli


def test_find_claude_exe_returns_cmd_when_only_cmd_present(monkeypatch) -> None:
    def fake_which(name: str) -> str | None:
        return r"C:\Users\Admin\AppData\Roaming\npm\claude.cmd" if name == "claude.cmd" else None

    monkeypatch.setattr(claude_cli.shutil, "which", fake_which)
    assert claude_cli.find_claude_exe() == r"C:\Users\Admin\AppData\Roaming\npm\claude.cmd"


def test_find_claude_exe_prefers_exe_over_cmd(monkeypatch) -> None:
    def fake_which(name: str) -> str | None:
        return {
            "claude": None,
            "claude.exe": r"C:\bin\claude.exe",
            "claude.cmd": r"C:\bin\claude.cmd",
        }[name]

    monkeypatch.setattr(claude_cli.shutil, "which", fake_which)
    assert claude_cli.find_claude_exe() == r"C:\bin\claude.exe"


def test_find_claude_exe_returns_none_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _name: None)
    assert claude_cli.find_claude_exe() is None


def test_hidden_subprocess_kwargs_windows(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli.os, "name", "nt")
    kwargs = claude_cli.hidden_subprocess_kwargs()
    assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
    startupinfo = kwargs["startupinfo"]
    assert startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert startupinfo.wShowWindow == subprocess.SW_HIDE


def test_hidden_subprocess_kwargs_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli.os, "name", "posix")
    assert claude_cli.hidden_subprocess_kwargs() == {}


def test_claude_env_strips_api_key_and_proxies(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-cleared")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "also-cleared")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    env = claude_cli.claude_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "HTTPS_PROXY" not in env
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_run_claude_uses_resolved_exe_and_stdin(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli, "find_claude_exe", lambda: r"C:\fake\claude.cmd")
    captured: dict = {}

    class FakeProc:
        def communicate(self, input: str, timeout: int) -> tuple[str, str]:
            captured["stdin"] = input
            return ("OUTPUT", "")
        returncode = 0

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(claude_cli.subprocess, "Popen", fake_popen)
    rc, stdout, stderr = claude_cli.run_claude("hello", timeout=10)
    assert rc == 0
    assert stdout == "OUTPUT"
    assert captured["stdin"] == "hello"
    assert captured["cmd"][0] == r"C:\fake\claude.cmd"
    assert "--print" in captured["cmd"]
    assert "--dangerously-skip-permissions" in captured["cmd"]
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"
    assert captured["kwargs"]["stdin"] == subprocess.PIPE


def test_run_claude_raises_filenotfound_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli, "find_claude_exe", lambda: None)
    with pytest.raises(FileNotFoundError):
        claude_cli.run_claude("x", timeout=1)


def test_run_claude_propagates_timeout(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli, "find_claude_exe", lambda: r"C:\fake\claude.cmd")

    class TimingOutProc:
        def communicate(self, input=None, timeout=None):
            if input is not None:
                raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
            return ("", "")
        def kill(self) -> None: ...
        returncode = -1

    monkeypatch.setattr(claude_cli.subprocess, "Popen", lambda *a, **kw: TimingOutProc())
    with pytest.raises(subprocess.TimeoutExpired):
        claude_cli.run_claude("x", timeout=1)
