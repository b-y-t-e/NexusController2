"""Windows integration: firewall rules and ADB reverse forwarding.

Both are best-effort conveniences. Neither may crash the server, and both report
what happened so the dashboard can tell the user to run as Administrator instead
of leaving them staring at a phone that will not connect.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Sequence

log = logging.getLogger(__name__)

FIREWALL_RULE_TCP = "NexusController TCP"
FIREWALL_RULE_UDP = "NexusController Discovery"
COMMAND_TIMEOUT = 10.0

_CREATE_NO_WINDOW = 0x08000000


def is_windows() -> bool:
    return os.name == "nt"


def is_admin() -> bool:
    """True when the process has an elevated token."""
    if not is_windows():
        return os.geteuid() == 0  # type: ignore[attr-defined]
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def run_hidden(args: Sequence[str], *, timeout: float = COMMAND_TIMEOUT) -> subprocess.CompletedProcess:
    """Run a console tool without flashing a window in a windowed build."""
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "check": False,
    }
    if is_windows():
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    return subprocess.run(list(args), **kwargs)  # noqa: S603 - fixed argv, no shell


@dataclass(frozen=True, slots=True)
class StepResult:
    ok: bool
    message: str


# --- firewall ---------------------------------------------------------------

class FirewallManager:
    """Adds inbound rules scoped to the **private** profile only.

    The original code opened the port on every profile, including ``public``,
    which is exactly the network where you least want an unauthenticated input
    server reachable. It also never removed the rule.
    """

    def __init__(self, tcp_port: int, udp_port: int) -> None:
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self._added = False

    def apply(self) -> StepResult:
        if not is_windows():
            return StepResult(True, "Firewall management skipped (not Windows)")
        if not is_admin():
            return StepResult(
                False,
                "Firewall rule needs Administrator rights — run tools/add_firewall_rule.bat once",
            )
        try:
            self.remove()
            for name, protocol, port in (
                (FIREWALL_RULE_TCP, "TCP", self.tcp_port),
                (FIREWALL_RULE_UDP, "UDP", self.udp_port),
            ):
                result = run_hidden([
                    "netsh", "advfirewall", "firewall", "add", "rule",
                    f"name={name}", "dir=in", "action=allow",
                    f"protocol={protocol}", f"localport={port}",
                    "profile=private",
                ])
                if result.returncode != 0:
                    return StepResult(False, f"netsh failed for {protocol} {port}: {result.stdout.strip()}")
            self._added = True
            return StepResult(True, f"Firewall opened on private networks (TCP {self.tcp_port}, UDP {self.udp_port})")
        except (OSError, subprocess.SubprocessError) as exc:
            return StepResult(False, f"Firewall configuration failed: {exc}")

    def remove(self) -> None:
        if not is_windows() or not is_admin():
            return
        for name in (FIREWALL_RULE_TCP, FIREWALL_RULE_UDP):
            try:
                run_hidden(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"])
            except (OSError, subprocess.SubprocessError) as exc:
                log.debug("could not delete firewall rule %s: %s", name, exc)
        self._added = False


# --- adb --------------------------------------------------------------------

def find_adb() -> str | None:
    """Locate ``adb`` on PATH or in the usual SDK install locations."""
    on_path = shutil.which("adb")
    if on_path:
        return on_path
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
        os.path.expandvars(r"%ANDROID_HOME%\platform-tools\adb.exe"),
        os.path.expandvars(r"%ANDROID_SDK_ROOT%\platform-tools\adb.exe"),
        r"C:\Program Files\Android\android-sdk\platform-tools\adb.exe",
    ]
    for candidate in candidates:
        if "%" not in candidate and os.path.isfile(candidate):
            return candidate
    return None


def setup_adb_reverse(port: int) -> StepResult:
    """Forward ``localhost:<port>`` on any attached phone back to this PC.

    USB mode works by the phone dialling ``127.0.0.1``; ``adb reverse`` is what
    makes that loopback address reach the PC. (The original client instead tried
    to *listen* on the same port on the phone, which ``adb reverse`` had already
    bound — so USB mode could never work.)
    """
    adb = find_adb()
    if adb is None:
        return StepResult(False, "ADB not found — USB mode unavailable (install platform-tools)")
    try:
        devices = run_hidden([adb, "devices"])
        attached = [
            line.split("\t")[0]
            for line in devices.stdout.splitlines()[1:]
            if line.strip() and line.strip().endswith("device")
        ]
        if not attached:
            return StepResult(False, "No phone connected over USB — Wi-Fi mode still works")
        result = run_hidden([adb, "reverse", f"tcp:{port}", f"tcp:{port}"])
        if result.returncode != 0:
            return StepResult(False, f"adb reverse failed: {result.stderr.strip() or result.stdout.strip()}")
        return StepResult(True, f"USB mode ready — {len(attached)} device(s), phone connects to 127.0.0.1")
    except (OSError, subprocess.SubprocessError) as exc:
        return StepResult(False, f"ADB setup failed: {exc}")


def remove_adb_reverse(port: int) -> None:
    adb = find_adb()
    if adb is None:
        return
    try:
        run_hidden([adb, "reverse", "--remove", f"tcp:{port}"])
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("could not remove adb reverse: %s", exc)
