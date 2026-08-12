"""Firewall helpers.

Nothing here touches the real firewall: ``netsh`` and ``ShellExecuteW`` are
replaced, and what is asserted is the command that *would* have run. That is the
part worth pinning — a rule opened on the wrong profile or the wrong port is a
security bug that no amount of manual clicking would reliably catch.
"""

import os
import subprocess

import pytest

from nexus_server import system
from nexus_server.system import (
    FIREWALL_RULE_TCP,
    FIREWALL_RULE_UDP,
    FirewallManager,
    StepResult,
    elevated_firewall_command,
    firewall_rules_present,
    open_firewall_elevated,
    rule_command,
)


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class TestRuleCommand:
    def test_opens_the_private_profile_only(self):
        """A public network is the last place this server should be reachable on."""
        command = rule_command(FIREWALL_RULE_TCP, "TCP", 6000)
        assert "profile=private" in command
        assert not any(part.startswith("profile=") and "public" in part for part in command)

    def test_public_is_never_the_default(self):
        """A public-profile rule applies on every public network, café included."""
        assert "profile=private" in rule_command(FIREWALL_RULE_TCP, "TCP", 6000)
        assert "profile=private,public" in rule_command(
            FIREWALL_RULE_TCP, "TCP", 6000, "private,public"
        )

    def test_is_inbound_and_scoped_to_one_port(self):
        command = rule_command(FIREWALL_RULE_UDP, "UDP", 6001)
        assert "dir=in" in command
        assert "localport=6001" in command
        assert "protocol=UDP" in command


class TestNetworkCategories:
    """The question nobody asks: a private-profile rule is void on a public network."""

    def test_reads_name_and_category(self, monkeypatch):
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(
            system, "run_hidden", lambda *a, **k: FakeCompleted(0, "G6 2\tPublic\nTailscale\tPrivate\n")
        )
        assert system.active_network_categories() == {"G6 2": "Public", "Tailscale": "Private"}

    def test_an_unusable_answer_is_empty_not_a_guess(self, monkeypatch):
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(system, "run_hidden", lambda *a, **k: FakeCompleted(1, ""))
        assert system.active_network_categories() == {}


class TestProfileForOneInterface:
    """Which network the server serves on, not which networks exist.

    A machine with a VPN adapter or a second Wi-Fi usually has several profiles,
    and "is any of them public?" dragged the answer to public on a private LAN —
    which then reported a perfectly good rule as missing.
    """

    def test_reads_the_category(self, monkeypatch):
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(system, "run_hidden", lambda *a, **k: FakeCompleted(0, "Public\n"))
        assert system.network_category_for("192.168.1.119") == "Public"

    def test_empty_answer_is_unknown(self, monkeypatch):
        """No profile for that address — not a guess of "private"."""
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(system, "run_hidden", lambda *a, **k: FakeCompleted(0, "\n"))
        assert system.network_category_for("9.9.9.9") is None

    def test_no_address_is_unknown(self, monkeypatch):
        monkeypatch.setattr(system, "is_windows", lambda: True)
        assert system.network_category_for("") is None


class TestElevatedCommand:
    def test_covers_both_ports(self):
        line = elevated_firewall_command(6000, 6001)
        assert "protocol=TCP localport=6000" in line
        assert "protocol=UDP localport=6001" in line
        assert "profile=private" in line

    def test_quotes_the_rule_names(self):
        """They contain a space, and cmd would otherwise split them."""
        line = elevated_firewall_command(6000, 6001)
        assert f'name="{FIREWALL_RULE_TCP}"' in line
        assert f'name="{FIREWALL_RULE_UDP}"' in line

    def test_deletes_before_adding_so_it_can_be_run_twice(self):
        line = elevated_firewall_command(6000, 6001)
        assert line.index("delete rule") < line.index("add rule")

    def test_is_a_single_command_line(self):
        """One line means one UAC prompt, not four."""
        assert "\n" not in elevated_firewall_command(6000, 6001)


class TestSystemShell:
    def test_is_absolute(self):
        """ShellExecute resolves a bare name against the current directory, and
        this one runs elevated — a cmd.exe dropped next to the app must not be
        handed Administrator rights."""
        shell = system.system_shell()
        assert os.path.isabs(shell)
        assert shell.lower().endswith("system32\\cmd.exe")

    def test_follows_systemroot(self, monkeypatch):
        monkeypatch.setenv("SystemRoot", r"E:\Windows")
        assert system.system_shell() == r"E:\Windows\System32\cmd.exe"

    def test_falls_back_when_systemroot_is_missing(self, monkeypatch):
        monkeypatch.delenv("SystemRoot", raising=False)
        assert system.system_shell() == r"C:\Windows\System32\cmd.exe"


class TestRulesPresent:
    def test_true_when_netsh_finds_both(self, monkeypatch):
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(
            system, "run_hidden", lambda *a, **k: FakeCompleted(0, "LocalPort: 6000 6001")
        )
        assert firewall_rules_present(6000, 6001) is True

    def test_false_when_a_rule_is_missing(self, monkeypatch):
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(system, "run_hidden", lambda *a, **k: FakeCompleted(1, "No rules match"))
        assert firewall_rules_present(6000, 6001) is False

    def test_the_profile_is_pushed_down_to_netsh(self, monkeypatch):
        """A private-profile rule is void on a public network.

        Without this the whole Public detection was dead in its own main case:
        the rules appear, the banner hides, and the port is still shut.
        """
        seen: list[list[str]] = []
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(
            system, "run_hidden", lambda args, **k: seen.append(args) or FakeCompleted(0, "6000 6001")
        )
        firewall_rules_present(6000, 6001, "public")
        assert all("profile=public" in call for call in seen)

    def test_false_when_the_rule_is_for_another_port(self, monkeypatch):
        """Rules are found by name, so one left over from an older port would
        otherwise report a shut port as open and hide the banner."""
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(
            system, "run_hidden", lambda *a, **k: FakeCompleted(0, "Port lokalny: 6000")
        )
        assert firewall_rules_present(7100, 7101) is False

    def test_none_when_the_answer_is_unknown(self, monkeypatch):
        """Not Windows, or netsh unavailable — "unknown" is not "blocked"."""
        monkeypatch.setattr(system, "is_windows", lambda: False)
        assert firewall_rules_present(6000, 6001) is None

        monkeypatch.setattr(system, "is_windows", lambda: True)

        def explode(*args, **kwargs):
            raise OSError("netsh is not on PATH")

        monkeypatch.setattr(system, "run_hidden", explode)
        assert firewall_rules_present(6000, 6001) is None


class TestOpenFirewallElevated:
    def test_applies_directly_when_already_admin(self, monkeypatch):
        """No point asking for rights the process already has."""
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(system, "is_admin", lambda: True)
        applied: list[FirewallManager] = []
        monkeypatch.setattr(
            FirewallManager, "apply", lambda self: applied.append(self) or StepResult(True, "done")
        )
        assert open_firewall_elevated(6000, 6001).ok is True
        assert len(applied) == 1

    def test_the_prompt_is_raised_through_powershell(self, monkeypatch):
        """Not ShellExecuteW.

        That call needs a message pump on the calling thread and the dashboard
        has none, so it sat there with no prompt and no error — the button stuck
        on "waiting for approval" for good.
        """
        seen: list[list[str]] = []
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(system, "is_admin", lambda: False)
        monkeypatch.setattr(
            system, "run_hidden", lambda args, **k: seen.append(args) or FakeCompleted(0)
        )
        assert open_firewall_elevated(6000, 6001).ok is True

        launcher = " ".join(seen[0])
        assert "powershell" in launcher
        assert "-Verb RunAs" in launcher
        assert "profile=private" in launcher

    def test_declining_is_an_answer_not_a_malfunction(self, monkeypatch):
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(system, "is_admin", lambda: False)
        monkeypatch.setattr(system, "run_hidden", lambda *a, **k: _Cancelled())
        result = open_firewall_elevated(6000, 6001)
        assert result.ok is False
        assert "declined" in result.message

    def test_an_unanswered_prompt_times_out(self, monkeypatch):
        """The user walked away from the UAC dialog."""
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(system, "is_admin", lambda: False)

        def hang(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="powershell", timeout=1)

        monkeypatch.setattr(system, "run_hidden", hang)
        result = open_firewall_elevated(6000, 6001)
        assert result.ok is False
        assert "unanswered" in result.message

    def test_public_is_only_ever_asked_for_explicitly(self, monkeypatch):
        seen: list[list[str]] = []
        monkeypatch.setattr(system, "is_windows", lambda: True)
        monkeypatch.setattr(system, "is_admin", lambda: False)
        monkeypatch.setattr(
            system, "run_hidden", lambda args, **k: seen.append(args) or FakeCompleted(0)
        )
        open_firewall_elevated(6000, 6001, include_public=True)
        assert "profile=private,public" in " ".join(seen[0])

    def test_refuses_politely_off_windows(self, monkeypatch):
        monkeypatch.setattr(system, "is_windows", lambda: False)
        assert open_firewall_elevated(6000, 6001).ok is False


class _Cancelled:
    """What PowerShell reports when the user says no to UAC."""

    returncode = 1
    stdout = ""
    stderr = "Start-Process : The operation was canceled by the user."
