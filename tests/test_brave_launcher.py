from __future__ import annotations

from unittest.mock import patch

import pytest

from core import brave_launcher
from core.brave_launcher import BraveLauncher


def _make() -> BraveLauncher:
    return BraveLauncher(
        exe_path=r"C:\fake\brave.exe",
        user_data_dir=r"D:\fake\brave-grok-profile",
        debug_port=9222,
        start_url="https://grok.com/imagine",
    )


def test_ensure_running_attaches_when_port_alive_and_profile_match() -> None:
    launcher = _make()
    with patch.object(brave_launcher, "is_port_alive", return_value=True), \
         patch.object(brave_launcher, "find_brave_pids", return_value=[1234, 5678]):
        launched = launcher.ensure_running()
    assert launched is False
    assert launcher.owned is True
    assert launcher.pids == [1234, 5678]


def test_ensure_running_attaches_read_only_when_no_profile_match() -> None:
    launcher = _make()
    with patch.object(brave_launcher, "is_port_alive", return_value=True), \
         patch.object(brave_launcher, "find_brave_pids", return_value=[]):
        launched = launcher.ensure_running()
    assert launched is False
    assert launcher.owned is False
    assert launcher.pids == []


def test_stop_is_noop_when_not_owned() -> None:
    launcher = _make()
    launcher._owned = False
    launcher._pids = [42]  # even if pids are present, no-op when not owned
    with patch.object(brave_launcher, "kill_pids") as mk:
        count = launcher.stop()
    assert count == 0
    mk.assert_not_called()


def test_stop_kills_tracked_pids_when_owned() -> None:
    launcher = _make()
    launcher._owned = True
    launcher._pids = [1000, 2000]
    with patch.object(brave_launcher, "kill_pids", return_value=2) as mk:
        count = launcher.stop()
    assert count == 2
    mk.assert_called_once_with([1000, 2000])
    assert launcher.owned is False
    assert launcher.pids == []


def test_ensure_running_raises_when_exe_missing() -> None:
    launcher = _make()  # exe_path points to nonexistent C:\fake\brave.exe
    with patch.object(brave_launcher, "is_port_alive", return_value=False):
        with pytest.raises(RuntimeError, match="Brave executable not found"):
            launcher.ensure_running()


def test_from_config_uses_defaults_when_keys_missing() -> None:
    launcher = BraveLauncher.from_config({})
    assert launcher.debug_port == 9222
    assert launcher.user_data_dir.endswith("brave-grok-profile")
    assert launcher.start_url == "https://grok.com/imagine"
