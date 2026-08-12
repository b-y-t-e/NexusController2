"""The dashboard's Python API.

The web UI calls these methods across the pywebview bridge, so their return
shapes are a contract with `web/app.js` and `web/designer.js`.
"""

import pytest

from nexus_server import system
from nexus_server.app import Api
from nexus_server.desktop import FakeDesktop
from nexus_server.devices import FakeBackend
from nexus_server.padconfig import PadConfig
from nexus_server.protocol import MAX_PLAYERS, Button, DeviceType


@pytest.fixture(autouse=True)
def no_real_firewall_calls(monkeypatch):
    """Keep the suite off the machine it runs on.

    ``firewall_status`` shells out to netsh and PowerShell; left alone, tests
    about something else entirely would spawn processes, take seconds, and give
    different answers on different machines. ``tests/test_system.py`` is where
    those functions are actually exercised, against fake output.
    """
    monkeypatch.setattr("nexus_server.system.firewall_rules_present", lambda *a: None)
    monkeypatch.setattr("nexus_server.system.third_party_firewalls", lambda: [])
    monkeypatch.setattr("nexus_server.system.active_network_categories", lambda: {})
    monkeypatch.setattr("nexus_server.system.network_category_for", lambda ip: None)


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("nexus_server.app.create_backend", lambda: None)
    instance = Api(FakeBackend(), simulated=True)
    instance.settings.manage_firewall = False
    instance.settings.manage_adb = False
    instance.settings.discovery_enabled = False
    yield instance
    instance.shutdown()


@pytest.fixture()
def api_with_desktop(tmp_path, monkeypatch):
    """An API whose desktop control has a real (recording) backend behind it."""
    monkeypatch.setenv("NEXUS_CONFIG_DIR", str(tmp_path))
    backend = FakeDesktop()
    monkeypatch.setattr("nexus_server.app.create_backend", lambda: backend)
    instance = Api(FakeBackend(), simulated=True)
    instance.settings.manage_firewall = False
    instance.settings.manage_adb = False
    instance.settings.discovery_enabled = False
    yield instance, backend
    instance.shutdown()


def _hold_a_bound_key(api, desktop, slot: int = 0) -> None:
    """Put slot ``slot`` in the state of holding a bound key down."""
    session = api.server.slots.sessions[slot]
    session.keys.set_bindings({"a": "space"})
    for key, pressed in session.keys.update(int(Button.SOUTH), 0):
        desktop.set_key(key, pressed)
    assert desktop.keys["space"] is True


class TestDesktopGate:
    """The gate moving must let go of whatever the old slot was holding.

    Both of these go through the dashboard API rather than the server method it
    delegates to: the wiring is the part that was missing, so it is the part
    worth guarding.
    """

    def test_moving_the_lock_to_another_slot_releases_held_input(self, api_with_desktop):
        api, desktop = api_with_desktop
        api.set_desktop_control(True)
        _hold_a_bound_key(api, desktop)

        api.set_desktop_slot(1)

        assert desktop.keys["space"] is False
        assert api.server.slots.sessions[0].keys.release() == []

    def test_disabling_desktop_control_releases_held_input(self, api_with_desktop):
        api, desktop = api_with_desktop
        api.set_desktop_control(True)
        _hold_a_bound_key(api, desktop)

        api.set_desktop_control(False)

        assert desktop.keys["space"] is False
        assert api.server.slots.sessions[0].keys.release() == []


class TestFirewallOffer:
    def test_status_reports_what_the_banner_needs(self, api, monkeypatch):
        monkeypatch.setattr("nexus_server.system.firewall_rules_present", lambda *a: False)
        status = api.firewall_status()
        assert status["open"] is False
        assert status["tcp"] == api.settings.port
        assert status["udp"] == api.settings.discovery_port
        assert "admin" in status and "windows" in status

    def test_the_check_is_cached(self, api, monkeypatch):
        """Two netsh calls per dashboard poll would be absurd."""
        calls = []
        monkeypatch.setattr(
            "nexus_server.system.firewall_rules_present",
            lambda *a: calls.append(a) or True,
        )
        api.firewall_status()
        api.firewall_status()
        assert len(calls) == 1

    def test_success_is_reported_only_once_the_rules_exist(self, api, monkeypatch):
        """ShellExecute returning is not an outcome — the rules appearing is.

        Reporting the launch as success is what made a blocked attempt look like
        a button that did nothing.
        """
        monkeypatch.setattr("nexus_server.app.FIREWALL_VERIFY_SECONDS", 0.5)
        monkeypatch.setattr(
            "nexus_server.system.open_firewall_elevated",
            lambda *a, **k: system.StepResult(True, "launched"),
        )
        monkeypatch.setattr("nexus_server.system.firewall_rules_present", lambda *a: False)
        monkeypatch.setattr("nexus_server.system.third_party_firewalls", lambda: [])
        monkeypatch.setattr("nexus_server.system.active_network_categories", lambda: {})
        monkeypatch.setattr("nexus_server.system.network_category_for", lambda ip: None)

        result = api.open_firewall()
        assert result["ok"] is False
        assert "not added" in result["message"]
        assert "add_firewall_rule.bat" in result["message"]

    def test_rules_for_the_wrong_profile_are_not_called_missing(self, api, monkeypatch):
        """They were added; they just do not cover the network this PC is on.

        Reporting "the elevated command did not run" sent the user hunting for a
        failure that had not happened, while the actual fix — one more button —
        went unmentioned.
        """
        monkeypatch.setattr("nexus_server.app.FIREWALL_VERIFY_SECONDS", 0.3)
        monkeypatch.setattr(
            "nexus_server.system.open_firewall_elevated",
            lambda *a, **k: system.StepResult(True, "launched"),
        )
        monkeypatch.setattr("nexus_server.system.network_category_for", lambda ip: "Public")
        monkeypatch.setattr(
            "nexus_server.system.firewall_rules_present",
            lambda tcp, udp, profile="private": profile == "private",
        )

        result = api.open_firewall()
        assert result["ok"] is False
        assert "only for private networks" in result["message"]
        assert "did not run" not in result["message"]

    def test_a_third_party_firewall_is_named_in_the_result(self, api, monkeypatch):
        """Opening the Windows port is not enough when ESET filters too."""
        monkeypatch.setattr("nexus_server.app.FIREWALL_VERIFY_SECONDS", 0.5)
        monkeypatch.setattr(
            "nexus_server.system.open_firewall_elevated",
            lambda *a, **k: system.StepResult(True, "launched"),
        )
        monkeypatch.setattr("nexus_server.system.firewall_rules_present", lambda *a: True)
        monkeypatch.setattr("nexus_server.system.third_party_firewalls", lambda: ["ESET Zapora"])
        monkeypatch.setattr("nexus_server.system.active_network_categories", lambda: {})
        monkeypatch.setattr("nexus_server.system.network_category_for", lambda ip: None)

        result = api.open_firewall()
        assert result["ok"] is True
        assert "ESET Zapora" in result["message"]

    def test_opening_invalidates_the_cache(self, api, monkeypatch):
        """Otherwise the banner would insist the port is shut for another 15 s."""
        monkeypatch.setattr("nexus_server.system.firewall_rules_present", lambda *a: False)
        monkeypatch.setattr("nexus_server.system.third_party_firewalls", lambda: [])
        monkeypatch.setattr("nexus_server.system.active_network_categories", lambda: {})
        monkeypatch.setattr("nexus_server.system.network_category_for", lambda ip: None)
        api.firewall_status()

        monkeypatch.setattr(
            "nexus_server.system.open_firewall_elevated",
            lambda *a, **k: system.StepResult(True, "opening"),
        )
        monkeypatch.setattr("nexus_server.system.firewall_rules_present", lambda *a: True)
        assert api.open_firewall()["ok"] is True
        assert api.firewall_status()["open"] is True


class TestState:
    def test_state_has_everything_the_ui_reads(self, api):
        state = api.get_state()
        expected = {
            "running", "players", "capacity", "connected", "pps", "ips", "theme",
            "version", "log", "component_sets", "device_types", "token_required",
            "desktop_control", "desktop_available", "simulated", "xinput_warning",
        }
        assert expected <= set(state)

    def test_component_sets_cover_every_device_type(self, api):
        sets = api.get_state()["component_sets"]
        assert set(sets) == {t.name for t in DeviceType}
        for entries in sets.values():
            assert entries and all("id" in e and "size" in e for e in entries)

    def test_players_are_reported_even_when_empty(self, api):
        players = api.get_state()["players"]
        assert len(players) == MAX_PLAYERS
        assert all(p["connected"] is False and p["config"] is None for p in players)


class TestPadConfigApi:
    def test_default_config_for_an_empty_slot(self, api):
        info = api.get_pad_config(0)
        assert info["connected"] is False
        assert info["reported"] is False
        assert info["config"]["type"] == "XBOX360"
        assert info["components"]
        assert info["aspect"] > 1

    def test_components_match_the_config_type(self, api):
        info = api.get_pad_config(0)
        ids = {c["id"] for c in info["components"]}
        assert ids == set(info["config"]["layout"])

    def test_get_components_by_name(self, api):
        ids = {c["id"] for c in api.get_components("BUZZ")}
        assert ids == {"BUZZ_RED", "BUZZ_BLUE", "BUZZ_ORANGE", "BUZZ_GREEN", "BUZZ_YELLOW"}

    def test_get_components_tolerates_a_wire_value(self, api):
        assert api.get_components(2) == api.get_components("BUZZ")

    def test_get_components_falls_back_on_nonsense(self, api):
        assert api.get_components("NINTENDO") == api.get_components("XBOX360")

    def test_push_to_an_empty_slot_reports_why(self, api):
        result = api.push_pad_config(0, PadConfig.default().to_dict())
        assert result == {"ok": False, "error": "Player not connected"}

    def test_push_rejects_a_malformed_document(self, api):
        result = api.push_pad_config(0, {"v": 77})
        assert result["ok"] is False
        assert "schema version" in result["error"]

    def test_push_to_all_with_nobody_connected(self, api):
        result = api.push_pad_config_to_all(PadConfig.default().to_dict())
        assert result["ok"] is False

    def test_set_device_type_on_an_empty_slot(self, api):
        assert api.set_pad_device_type(0, "BUZZ")["ok"] is False

    def test_set_device_type_rejects_a_bad_slot(self, api):
        assert api.set_pad_device_type(99, "BUZZ") == {"ok": False, "error": "No such slot"}

    def test_reset_layout_rejects_a_bad_slot(self, api):
        assert api.reset_pad_layout(-5)["ok"] is False


class TestProfiles:
    def _doc(self, device_type=DeviceType.XBOX360, x=0.5):
        config = PadConfig.default(device_type)
        config.layout[next(iter(config.layout))]["x"] = x
        return config.to_dict()

    def test_save_then_list(self, api):
        result = api.save_profile("Racing", self._doc())
        assert result["ok"] is True
        assert "Racing" in result["profiles"]
        assert api.list_profiles() == ["Racing"]

    def test_profiles_survive_a_reload(self, api):
        api.save_profile("Racing", self._doc())
        assert api.store.load().pad_profiles.keys() == {"Racing"}

    def test_saved_profile_keeps_the_layout(self, api):
        api.save_profile("Tweaked", self._doc(x=0.13))
        loaded = api.load_profile("Tweaked")
        assert loaded["ok"] is True
        assert 0.13 in [p["x"] for p in loaded["config"]["layout"].values()]

    def test_buzz_profile_round_trips(self, api):
        api.save_profile("Quiz", self._doc(DeviceType.BUZZ))
        assert api.load_profile("Quiz")["config"]["type"] == "BUZZ"

    def test_nameless_profile_is_refused(self, api):
        assert api.save_profile("   ", self._doc())["ok"] is False

    def test_malformed_profile_is_refused(self, api):
        assert api.save_profile("Bad", {"v": 3})["ok"] is False

    def test_long_name_is_truncated(self, api):
        api.save_profile("x" * 200, self._doc())
        assert all(len(name) <= 48 for name in api.list_profiles())

    def test_saving_twice_overwrites(self, api):
        api.save_profile("P", self._doc(x=0.2))
        api.save_profile("P", self._doc(x=0.8))
        assert api.list_profiles() == ["P"]
        assert 0.8 in [p["x"] for p in api.load_profile("P")["config"]["layout"].values()]

    def test_delete(self, api):
        api.save_profile("Gone", self._doc())
        assert api.delete_profile("Gone")["profiles"] == []

    def test_deleting_something_absent_is_harmless(self, api):
        assert api.delete_profile("never existed")["ok"] is True

    def test_load_missing_profile(self, api):
        assert api.load_profile("nope") == {"ok": False, "error": "No such profile"}

    def test_apply_missing_profile(self, api):
        assert api.apply_profile("nope")["ok"] is False

    def test_apply_with_nobody_connected(self, api):
        api.save_profile("P", self._doc())
        assert api.apply_profile("P")["ok"] is False
        assert api.apply_profile("P", 0)["ok"] is False


class TestSettingsApi:
    def test_desktop_control_cannot_be_enabled_without_a_backend(self, api):
        result = api.set_desktop_control(True)
        assert result["ok"] is False
        assert api.settings.desktop_control is False

    def test_haptics_toggle_persists(self, api):
        api.set_haptics(False)
        assert api.store.load().haptics is False

    def test_desktop_slot_is_clamped(self, api):
        assert api.set_desktop_slot(99) == MAX_PLAYERS - 1
        assert api.set_desktop_slot(-4) == 0

    def test_key_bind_round_trip(self, api):
        assert api.set_key_bind(0, "a", "space")["ok"] is True
        assert api.get_bindings(0) == {"a": "space"}
        api.set_key_bind(0, "a", "")
        assert api.get_bindings(0) == {}

    def test_unknown_button_is_refused(self, api):
        assert api.set_key_bind(0, "nonsense", "x")["ok"] is False

    def test_clear_bindings(self, api):
        api.set_key_bind(1, "b", "enter")
        api.clear_bindings(1)
        assert api.get_bindings(1) == {}

    def test_token_regeneration_changes_the_token(self, api):
        before = api.settings.token
        assert api.regenerate_token() != before

    def test_test_rumble_on_an_empty_slot(self, api):
        assert api.test_rumble(0) is False
        assert api.test_rumble(99) is False
