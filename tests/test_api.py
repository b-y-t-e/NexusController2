"""The dashboard's Python API.

The web UI calls these methods across the pywebview bridge, so their return
shapes are a contract with `web/app.js` and `web/designer.js`.
"""

import pytest

from nexus_server.app import Api
from nexus_server.devices import FakeBackend
from nexus_server.padconfig import PadConfig
from nexus_server.protocol import DeviceType


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
        assert len(players) == 4
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
        assert api.set_desktop_slot(99) == 3
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
