"""Windows integration: firewall rules and ADB reverse forwarding.

Both are best-effort conveniences. Neither may crash the server, and both report
what happened so the dashboard can tell the user to run as Administrator instead
of leaving them staring at a phone that will not connect.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
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
        # A localised Windows answers in its own code page, and netsh output has
        # bytes that the console encoding cannot always decode. Without this the
        # UnicodeDecodeError travels all the way out through the dashboard bridge
        # and takes the firewall check down with it — on exactly the machines the
        # check matters most.
        "errors": "replace",
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

    def __init__(self, tcp_port: int, udp_port: int, *, profile: str = "private") -> None:
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.profile = profile
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
                result = run_hidden(rule_command(name, protocol, port, self.profile))
                if result.returncode != 0:
                    return StepResult(False, f"netsh failed for {protocol} {port}: {result.stdout.strip()}")
            self._added = True
            return StepResult(
                True,
                f"Firewall opened on {self.profile} networks "
                f"(TCP {self.tcp_port}, UDP {self.udp_port})",
            )
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


def rule_command(name: str, protocol: str, port: int, profile: str = "private") -> list[str]:
    """The ``netsh`` argv that opens one port on the given firewall profiles.

    Shared by the in-process path and the elevated one so the two can never drift
    into opening different things. ``private`` is the default and the only one
    the app ever chooses by itself.
    """
    return [
        "netsh", "advfirewall", "firewall", "add", "rule",
        f"name={name}", "dir=in", "action=allow",
        f"protocol={protocol}", f"localport={port}",
        f"profile={profile}",
    ]


def network_category_for(ip: str) -> str | None:
    """The firewall profile of the network reached through ``ip``.

    Asking "is *any* network public?" is not the same question: a machine with a
    VPN adapter, a hotspot and an Ethernet cable usually has several, and the one
    that matters is the interface the server is bound to. Getting that wrong made
    the dashboard check the public profile on a private LAN and then report a
    successful open as a failure.

    ``None`` when it cannot be determined.
    """
    if not is_windows() or not ip:
        return None
    query = (
        f"$a = Get-NetIPAddress -IPAddress '{ip}' -ErrorAction SilentlyContinue;"
        " if ($a) { Get-NetConnectionProfile -InterfaceIndex $a.InterfaceIndex"
        " -ErrorAction SilentlyContinue | Select-Object -ExpandProperty NetworkCategory }"
    )
    try:
        result = run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", query]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("could not read the profile for %s: %s", ip, exc)
        return None
    if result.returncode != 0:
        return None
    category = result.stdout.strip().splitlines()
    return category[0].strip() if category and category[0].strip() else None


def active_network_categories() -> dict[str, str]:
    """``{network name: "Public" | "Private" | "DomainAuthenticated"}``, best effort.

    This is the question nobody thinks to ask. Firewall rules are scoped to a
    *profile*, not to a network, so a rule for the private profile does nothing
    at all while Windows has the current Wi-Fi filed under Public — which is what
    it does by default for a phone hotspot. The port then stays shut with every
    rule apparently in place.
    """
    if not is_windows():
        return {}
    query = (
        "Get-NetConnectionProfile | ForEach-Object "
        "{ \"$($_.Name)`t$($_.NetworkCategory)\" }"
    )
    try:
        result = run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", query]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("could not read network profiles: %s", exc)
        return {}
    if result.returncode != 0:
        return {}
    categories: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, separator, category = line.partition("\t")
        if separator and name.strip():
            categories[name.strip()] = category.strip()
    return categories


def firewall_rules_present(
    tcp_port: int, udp_port: int, profile: str = "private"
) -> bool | None:
    """Whether both rules exist **for these ports, on this profile**.

    ``None`` means "cannot tell" (not Windows, or netsh failed).

    All three qualifiers matter, and each of them has been the answer to "the
    rule is right there and the port is still shut":

    * the ports, because rules are found by name and one left from an earlier
      port would otherwise report a shut port as open;
    * the profile, because a private-profile rule does nothing while Windows has
      the current network filed under Public — the commonest case of all, since
      that is what it calls a phone hotspot.

    ``profile`` is pushed down to netsh rather than parsed out of its output:
    the field labels are localised ("Port lokalny" on a Polish Windows) but the
    exit code is not.
    """
    if not is_windows():
        return None
    try:
        for name, port in ((FIREWALL_RULE_TCP, tcp_port), (FIREWALL_RULE_UDP, udp_port)):
            result = run_hidden([
                "netsh", "advfirewall", "firewall", "show", "rule",
                f"name={name}", f"profile={profile}",
            ])
            # netsh exits non-zero and says "No rules match" when nothing matches
            # the name *and* profile together.
            if result.returncode != 0:
                return False
            if not re.search(rf"\b{port}\b", result.stdout):
                return False
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("could not query firewall rules: %s", exc)
        return None
    return True


def elevated_firewall_command(tcp_port: int, udp_port: int, *, profile: str = "private") -> str:
    """The single ``cmd /c`` line an elevated shell runs to open both ports.

    One command, so Windows asks for consent once rather than four times. The
    deletes make it idempotent: running it twice leaves one rule, not duplicates.
    """
    parts = [
        f'netsh advfirewall firewall delete rule name="{name}" >nul 2>&1'
        for name in (FIREWALL_RULE_TCP, FIREWALL_RULE_UDP)
    ]
    for name, protocol, port in (
        (FIREWALL_RULE_TCP, "TCP", tcp_port),
        (FIREWALL_RULE_UDP, "UDP", udp_port),
    ):
        # The rule name contains a space, so it has to be quoted for cmd; every
        # other argument comes from rule_command() unchanged.
        arguments = " ".join(
            f'name="{name}"' if part.startswith("name=") else part
            for part in rule_command(name, protocol, port, profile)[1:]
        )
        parts.append(f"netsh {arguments}")
    return " & ".join(parts)


def system_shell() -> str:
    """Absolute path to the real ``cmd.exe``.

    Never the bare name. ShellExecute resolves an unqualified path against the
    process's current directory among others, and this call runs its target
    **elevated** — so a ``cmd.exe`` sitting next to a downloaded copy of the app
    would be handed Administrator rights by a user who thought they were opening
    a firewall port.
    """
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(root, "System32", "cmd.exe")


def _elevate(command: str, *, timeout: float = 120.0) -> StepResult:
    """Run ``command`` in an elevated shell, via PowerShell's ``Start-Process -Verb RunAs``.

    Not ``ShellExecuteW``. That call needs a message pump on the calling thread,
    and the dashboard calls this from a pywebview worker thread that has none —
    so it would sit there with no UAC prompt ever appearing and no error either,
    leaving the button stuck on "waiting for approval" forever.

    PowerShell raises the prompt in its own process, which has a pump, and tells
    us plainly what happened: exit 0 for accepted, a message containing
    "canceled" when the user says no.
    """
    if not is_windows():
        return StepResult(False, "Elevation is a Windows feature")
    escaped = command.replace("'", "''")
    launcher = (
        f"Start-Process -FilePath '{system_shell()}' "
        f"-ArgumentList '/c {escaped}' -Verb RunAs -WindowStyle Hidden -Wait"
    )
    try:
        result = run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", launcher],
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return StepResult(False, "The Administrator prompt was left unanswered")
    except (OSError, subprocess.SubprocessError) as exc:
        log.exception("could not launch the elevated command")
        return StepResult(False, f"Could not ask for Administrator rights: {exc}")

    if result.returncode == 0:
        return StepResult(True, "elevated command finished")
    text = (result.stderr or "") + (result.stdout or "")
    if "cancel" in text.lower() or "anulow" in text.lower():
        return StepResult(False, "Administrator approval was declined — ports left closed")
    return StepResult(False, f"The elevated command failed: {text.strip()[:200]}")



def third_party_firewalls() -> list[str]:
    """Names of non-Microsoft firewalls Windows knows about, best effort.

    Purely diagnostic. A product like ESET filters traffic *in addition to* the
    Windows rules, so opening a port here can succeed and the phone still not get
    through — which looks like the app lying to the user unless we say so.
    """
    if not is_windows():
        return []
    query = (
        "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName FirewallProduct"
        " | Select-Object -ExpandProperty displayName"
    )
    try:
        result = run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", query]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("could not list firewall products: %s", exc)
        return []
    if result.returncode != 0:
        return []
    names = {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and "windows" not in line.lower()
    }
    return sorted(names)


def open_firewall_elevated(
    tcp_port: int, udp_port: int, *, include_public: bool = False
) -> StepResult:
    """Ask Windows to run the rule-adding command as Administrator.

    The prompt is raised through PowerShell rather than ShellExecuteW — see
    :func:`_elevate` for why the direct call could never show one from here.

    ``include_public`` widens the rule to the public profile. Never chosen by the
    app: a public-profile rule applies to *every* public network the machine ever
    joins, café included, so it takes a person deciding that their phone hotspot
    is worth it.
    """
    if not is_windows():
        return StepResult(False, "Firewall rules are a Windows feature")
    profile = "private,public" if include_public else "private"
    if is_admin():
        return FirewallManager(tcp_port, udp_port, profile=profile).apply()

    result = _elevate(elevated_firewall_command(tcp_port, udp_port, profile=profile))
    if not result.ok:
        return result
    return StepResult(
        True, f"Opened TCP {tcp_port} and UDP {udp_port} on {profile} networks"
    )


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
