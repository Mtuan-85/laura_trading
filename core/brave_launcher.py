"""PID-tracking Brave launcher.

Spawns Brave directly (not via .bat) so we can identify, by command-line
match, the brave.exe processes belonging to OUR profile. Only those PIDs
get killed on shutdown — the user's normal Brave (a separate process tree
on a different profile) is never touched.

Ownership semantics
-------------------
- If CDP port is already alive when we start AND a brave.exe is running with
  our --user-data-dir, we adopt those PIDs as "owned" (likely a leftover
  from a previous run that we should be able to kill).
- If the port is alive but no brave matches our profile, someone else is on
  that port — we attach read-only and never kill on stop().
- If the port is dead, we launch detached + record the PIDs whose CommandLine
  contains our user_data_dir.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

from loguru import logger as log


def is_port_alive(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except (OSError, ConnectionError):
        return False


def find_brave_pids(user_data_dir: str) -> list[int]:
    """Return brave.exe PIDs whose CommandLine contains --user-data-dir=<our dir>.

    Windows-only (returns [] elsewhere). Uses PowerShell + WMI.
    """
    if os.name != "nt":
        return []
    # Use the user_data_dir as a literal substring match. The dir is unique to
    # our profile so casual collisions are unlikely.
    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'brave.exe' -and "
        f"$_.CommandLine -like '*{user_data_dir}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        log.warning(f"[brave] find_pids failed: {e}")
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def kill_pids(pids: list[int]) -> int:
    """taskkill /F /T each pid. Returns count of kill commands issued."""
    if os.name != "nt" or not pids:
        return 0
    count = 0
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=5,
            )
            log.info(f"[brave] killed pid={pid}")
            count += 1
        except Exception as e:
            log.warning(f"[brave] taskkill pid={pid} failed: {e}")
    return count


class BraveLauncher:
    """Launches Brave + tracks PIDs by --user-data-dir match.

    Use as::

        launcher = BraveLauncher.from_config(config["brave"])
        launcher.ensure_running()        # idempotent — attaches or spawns
        # ...
        launcher.stop()                  # kills only what's ours
    """

    def __init__(
        self,
        exe_path: str,
        user_data_dir: str,
        debug_port: int,
        start_url: str = "https://grok.com/imagine",
    ) -> None:
        self.exe_path = exe_path
        self.user_data_dir = user_data_dir
        self.debug_port = int(debug_port)
        self.start_url = start_url
        self._owned = False
        self._pids: list[int] = []

    @classmethod
    def from_config(cls, brave_cfg: dict) -> "BraveLauncher":
        return cls(
            exe_path=brave_cfg.get("exe", r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"),
            user_data_dir=brave_cfg.get("user_data_dir", r"D:\CDP_Browser\brave-grok-profile"),
            debug_port=int(brave_cfg.get("debug_port", 9222)),
            start_url=brave_cfg.get("start_url", "https://grok.com/imagine"),
        )

    @property
    def owned(self) -> bool:
        return self._owned

    @property
    def pids(self) -> list[int]:
        return list(self._pids)

    def ensure_running(self, wait_timeout: float = 30.0) -> bool:
        """Make sure Brave is reachable on debug_port. Returns True if newly launched."""
        if is_port_alive(self.debug_port):
            pids = find_brave_pids(self.user_data_dir)
            if pids:
                log.info(
                    f"[brave] port {self.debug_port} alive, profile match pids={pids} — adopting as owned"
                )
                self._owned = True
                self._pids = pids
            else:
                log.info(
                    f"[brave] port {self.debug_port} alive but no profile match — attaching read-only (will not kill)"
                )
                self._owned = False
                self._pids = []
            return False

        exe = Path(self.exe_path)
        if not exe.exists():
            raise RuntimeError(f"Brave executable not found: {self.exe_path}")

        args = [
            str(exe),
            f"--remote-debugging-port={self.debug_port}",
            f"--user-data-dir={self.user_data_dir}",
            "--no-first-run",
            self.start_url,
        ]
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
                | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            )
        log.info(f"[brave] launching detached: {args}")
        subprocess.Popen(
            args,
            creationflags=creationflags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )

        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            if is_port_alive(self.debug_port):
                pids = find_brave_pids(self.user_data_dir)
                if pids:
                    self._owned = True
                    self._pids = pids
                    log.info(f"[brave] launched + tracked pids={pids}")
                    return True
                # Port up but PID discovery still racing — give it a beat.
            time.sleep(0.5)
        raise RuntimeError(
            f"Brave CDP port {self.debug_port} not ready after {wait_timeout}s"
        )

    def stop(self) -> int:
        """Kill all tracked Brave PIDs. No-op if not owned. Returns kill count."""
        if not self._owned or not self._pids:
            log.debug(f"[brave] stop() skipped (owned={self._owned}, pids={self._pids})")
            return 0
        log.info(f"[brave] stopping pids={self._pids} (profile={self.user_data_dir})")
        count = kill_pids(self._pids)
        self._pids = []
        self._owned = False
        return count

    def relaunch(self, wait_timeout: float = 30.0) -> None:
        """Stop (if owned) + ensure_running again. Used by retry recovery path."""
        self.stop()
        time.sleep(1.5)
        self.ensure_running(wait_timeout=wait_timeout)
