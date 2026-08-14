"""The dashboard's Python API.

The web UI calls these methods across the pywebview bridge, so their return
shapes are a contract with `web/app.js` and `web/designer.js`.
"""

import threading
import time

import pytest

from nexus_server import autostart, system, updates
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


def _settled(api, timeout: float = 2.0) -> dict:
    """The update state once the background check has finished.

    Polled rather than joined: the thread is deliberately not exposed — the page
    polls for the answer too, so this is the same contract it has.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = api.update_status()
        if status["state"] != "checking":
            return status
        time.sleep(0.01)
    raise AssertionError("the update check never finished")


def _writes(payload: bytes):
    """A stand-in for ``updates.download_to``: puts the bytes where it was told to."""
    def download_to(release, dest, *args, **kwargs):
        dest.write_bytes(payload)
        return dest

    return download_to


class TestAutostartAndTray:
    """The two switches that decide whether the app is there when you want it."""

    def test_a_source_checkout_reports_why_it_cannot_register(self, api):
        status = api.autostart_status()
        assert status["supported"] is False
        assert status["enabled"] is False
        assert status["reason"]

    def test_toggling_from_a_source_checkout_changes_nothing(self, api):
        """No .exe to name in the entry — and no silent failure either."""
        assert api.set_autostart(True)["enabled"] is False

    def test_a_frozen_build_registers_and_unregisters(self, api, tmp_path, monkeypatch):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"build")
        registry: dict[str, str] = {}

        class Key:
            def get(self, name):
                return registry.get(name)

            def set(self, name, value):
                registry[name] = value

            def delete(self, name):
                registry.pop(name, None)

        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.autostart.os.name", "nt")
        monkeypatch.setattr("nexus_server.autostart.default_key", Key)

        assert api.set_autostart(True)["enabled"] is True
        assert registry[autostart.VALUE_NAME] == autostart.command_for(exe)
        assert registry[autostart.VALUE_NAME].endswith(autostart.MINIMIZED_FLAG)
        assert api.set_autostart(False)["enabled"] is False
        assert registry == {}
        assert any("start with Windows" in line for line in api.get_log())

    def test_a_registry_that_refuses_is_reported_not_raised(self, api, tmp_path, monkeypatch):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"build")

        class Locked:
            def get(self, name):
                return None

            def set(self, name, value):
                raise PermissionError("access is denied")

            def delete(self, name):
                raise PermissionError("access is denied")

        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.autostart.os.name", "nt")
        monkeypatch.setattr("nexus_server.autostart.default_key", Locked)

        answer = api.set_autostart(True)
        assert answer["enabled"] is False
        assert "denied" in answer["error"]

    def test_the_tray_switch_says_when_there_is_no_tray(self, api):
        """Headless and every test: no icon, so closing cannot mean hiding."""
        state = api.window_state()
        assert state["tray_running"] is False
        assert state["close_to_tray"] is True     # the setting, which is not the same thing

    def test_the_icon_is_not_the_page_s_to_stop(self, api):
        """pywebview publishes every public attribute of this object and recurses
        into what it finds, so a public `tray` would put `api.tray.stop()` on the
        page — an icon the dashboard can remove while the window is open, leaving
        a close that hides into nothing."""
        assert not hasattr(api, "tray")
        assert not hasattr(api, "quitting")

    def test_the_setting_is_remembered(self, api):
        assert api.set_close_to_tray(False) is False
        assert api.store.load().close_to_tray is False

    def test_quitting_is_not_a_close_to_the_tray(self, api):
        """quit() is the tray's own Quit and the second half of an update: the
        new build is already running and wants the port this one holds."""
        api.quit()
        assert api._quitting is True


class FakeServer:
    """A server that records what was asked of it, and can refuse to bind."""

    def __init__(self, refuse: OSError | None = None) -> None:
        self.running = False
        self.starts = 0
        self.stops = 0
        self.refuse = refuse

    def start(self) -> None:
        self.starts += 1
        if self.refuse is not None:
            raise self.refuse
        self.running = True

    def stop(self) -> None:
        self.stops += 1
        self.running = False


class TestResumingTheServer:
    """Where the last session left the server is where the next one starts.

    Not a snapshot of a moment but the user's last decision: Stop is the only
    thing that means "stopped". Quitting, closing the window, an update swapping
    the build, Windows shutting down mid-game — none of those are anybody saying
    stop, and after any of them the phone should find the server there again.
    """

    def _api(self, api, refuse: OSError | None = None) -> FakeServer:
        server = FakeServer(refuse)
        api.server = server
        return server

    def test_starting_is_remembered(self, api):
        self._api(api)
        assert api.start_server()["ok"] is True
        assert api.settings.server_running is True
        assert api.store.load().server_running is True, "it has to survive the process"

    def test_a_port_that_was_taken_is_not_a_state_to_restore(self, api):
        """The next start would fail the same way, with nobody watching."""
        self._api(api, refuse=OSError("address already in use"))
        assert api.start_server()["ok"] is False
        assert api.settings.server_running is False
        assert api.store.load().server_running is False

    def test_stopping_is_remembered_too(self, api):
        server = self._api(api)
        api.start_server()
        assert api.stop_server()["ok"] is True
        assert server.stops == 1
        assert api.settings.server_running is False
        assert api.store.load().server_running is False

    def test_it_comes_back_up_when_that_is_where_it_was_left(self, api):
        server = self._api(api)
        api.settings.server_running = True
        assert api.resume_server()["restored"] is True
        assert server.running is True
        assert any("restored" in line.lower() for line in api.get_log())

    def test_a_server_the_user_stopped_stays_stopped(self, api):
        server = self._api(api)
        api.settings.server_running = False
        assert api.resume_server()["restored"] is False
        assert server.starts == 0

    def test_it_never_starts_one_that_is_already_running(self, api):
        server = self._api(api)
        server.running = True
        api.settings.server_running = True
        assert api.resume_server()["restored"] is False
        assert server.starts == 0

    def test_a_failure_to_restore_is_reported_not_raised(self, api):
        """A dock unplugged since yesterday takes the bound address with it. The
        dashboard is about to open with a Start button on it; this only has to
        say why it did not press it."""
        self._api(api, refuse=OSError("cannot assign requested address"))
        api.settings.server_running = True
        answer = api.resume_server()
        assert answer["restored"] is False
        assert "cannot assign" in answer["error"]
        assert any("Could not restore" in line for line in api.get_log())

    def test_the_intent_survives_a_failed_restore(self, api):
        """The dock comes back tomorrow; forgetting here would mean the user has
        to remember instead."""
        self._api(api, refuse=OSError("cannot assign requested address"))
        api.settings.server_running = True
        api.resume_server()
        assert api.settings.server_running is True


class TestBindingEveryInterface:
    """A PC on two networks at once serves one of them today.

    A docked laptop with Ethernet and Wi-Fi, a desktop with a second adapter: the
    listener takes one address and the phone on the other network finds nothing,
    with no error anywhere to explain it.
    """

    def test_the_dashboard_word_for_it_is_stored_as_the_wildcard(self, api):
        api.server = FakeServer()
        assert api.start_server("ALL")["ok"] is True
        assert api.settings.bind_ip == "0.0.0.0"
        assert api.store.load().bind_ip == "0.0.0.0"

    def test_auto_and_a_named_address_still_mean_what_they_did(self, api):
        api.server = FakeServer()
        api.start_server("192.168.1.50")
        assert api.settings.bind_ip == "192.168.1.50"
        api.start_server("AUTO")
        assert api.settings.bind_ip == ""

    def test_nonsense_is_still_refused(self, api):
        api.server = FakeServer()
        answer = api.start_server("not-an-address")
        assert answer["ok"] is False and "IPv4" in answer["error"]

    def test_a_phone_is_never_told_to_dial_the_wildcard(self):
        """0.0.0.0 is where to listen; it is nowhere to connect to. The QR code
        and the pairing line carry the LAN address, exactly as before."""
        from nexus_server import netinfo

        assert netinfo.advertised_ip("0.0.0.0") == netinfo.primary_ip()
        assert netinfo.advertised_ip("") == netinfo.primary_ip()
        assert netinfo.advertised_ip("192.168.1.50") == "192.168.1.50"


class TestUpdates:
    """The dashboard's half of updating: state the page can render, and no surprises.

    The network is never touched — `tests/test_updates.py` covers what happens on
    the wire. What matters here is that constructing the API does not reach for
    it either, that a check runs off the UI thread, and that install refuses the
    two situations where it cannot work rather than half-doing it.
    """

    def test_creating_the_api_does_not_call_github(self, tmp_path, monkeypatch):
        """The suite builds hundreds of these; a check here would be hundreds of
        DNS timeouts on a machine with no network. It belongs in run_gui()."""
        monkeypatch.setenv("NEXUS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("nexus_server.app.create_backend", lambda: None)
        called = []
        monkeypatch.setattr(
            "nexus_server.updates.fetch_latest", lambda **k: called.append(k) or None
        )
        instance = Api(FakeBackend(), simulated=True)
        try:
            assert instance.settings.check_updates is True
            assert called == []
        finally:
            instance.shutdown()

    def test_an_unexpected_failure_does_not_jam_checking_for_ever(self, api, monkeypatch):
        """"checking" is a state only the worker can leave.

        It catches UpdateError, so a bug anywhere else — in the parsing, in the
        comparison, in a library — escaped a daemon thread where nobody sees a
        traceback, and left the state set. check_for_update() then refuses to
        start while it is set, so every later check, for the life of the process,
        did nothing at all. The button just stopped working.
        """
        def explode(**kwargs):
            raise ValueError("something nobody thought of")

        monkeypatch.setattr("nexus_server.updates.fetch_latest", explode)
        api.check_for_update()
        status = _settled(api)
        assert status["state"] == "error"
        assert "something nobody thought of" in status["error"]

        # And the next check must actually run.
        release = updates.Release(
            tag="v99.0.0",
            assets={"NexusController.exe": updates.DOWNLOAD_PREFIX + "v99.0.0/NexusController.exe"},
        )
        monkeypatch.setattr("nexus_server.updates.fetch_latest", lambda **k: release)
        api.check_for_update()
        assert _settled(api)["state"] == "available"

    def test_a_check_whose_thread_will_not_start_is_not_left_checking(
        self, api, monkeypatch
    ):
        """The state is set before the worker exists, and only the worker clears it.

        So a thread that cannot start — the machine is out of them, which the
        accept loop already treats as a real possibility — left "checking" set
        with nothing running to change it, and every later check returned early
        for the life of the process.
        """
        class RefusesToStart(threading.Thread):
            def start(self):
                raise RuntimeError("can't start new thread")

        # Handed to this Api only: replacing threading.Thread for the whole
        # process would take the suite's own machinery with it.
        api._thread_factory = RefusesToStart
        status = api.check_for_update()

        assert status["state"] == "error"
        assert "could not start" in status["error"]

    def test_an_unexpected_failure_while_installing_leaves_the_state_usable(
        self, api, monkeypatch, tmp_path
    ):
        """Worse than the check: "installing" disables the button as well."""
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old")
        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.updates.writable", lambda p, **k: True)

        def explode(**kwargs):
            raise ValueError("the wheels came off")

        monkeypatch.setattr("nexus_server.updates.fetch_latest", explode)
        result = api.install_update()

        assert result["ok"] is False
        assert "the wheels came off" in result["error"]
        assert api.update_status()["state"] == "error"

    def test_status_starts_idle_and_names_this_build(self, api):
        from nexus_server import __version__

        status = api.update_status()
        assert status["state"] == "idle"
        assert status["current"] == __version__
        assert status["url"].startswith("https://github.com/")

    def test_a_check_that_finds_a_newer_release(self, api, monkeypatch):
        release = updates.Release(
            tag="v99.0.0",
            assets={"NexusController.exe": updates.DOWNLOAD_PREFIX + "v99.0.0/NexusController.exe"},
        )
        monkeypatch.setattr("nexus_server.updates.fetch_latest", lambda **k: release)
        api.check_for_update()
        status = _settled(api)
        assert status["state"] == "available"
        assert status["latest"] == "99.0.0"
        assert status["has_asset"] is True

    def test_an_older_release_is_not_offered(self, api, monkeypatch):
        release = updates.Release(tag="v0.1.0", assets={})
        monkeypatch.setattr("nexus_server.updates.fetch_latest", lambda **k: release)
        api.check_for_update()
        assert _settled(api)["state"] == "none"

    def test_no_releases_at_all_is_not_an_error(self, api, monkeypatch):
        monkeypatch.setattr("nexus_server.updates.fetch_latest", lambda **k: None)
        api.check_for_update()
        assert _settled(api)["state"] == "none"

    def test_being_offline_is_recorded_quietly(self, api, monkeypatch):
        """No dialog, no log line — this app is meant to work with no internet."""
        def offline(**kwargs):
            raise updates.UpdateError("could not reach GitHub")

        monkeypatch.setattr("nexus_server.updates.fetch_latest", offline)
        api.check_for_update()
        status = _settled(api)
        assert status["state"] == "error"
        assert "GitHub" in status["error"]
        assert not any("update" in line.lower() for line in api.get_log())

    def test_installing_from_a_source_checkout_is_refused(self, api):
        """There is no .exe to replace; the page offers the download page instead."""
        result = api.install_update()
        assert result["ok"] is False
        assert "source checkout" in result["error"]

    def test_installing_where_it_cannot_write_is_refused(self, api, tmp_path, monkeypatch):
        exe = tmp_path / "NexusController.exe"
        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.updates.writable", lambda path, **k: False)
        result = api.install_update()
        assert result["ok"] is False
        assert "No permission" in result["error"]

    def test_a_successful_install_swaps_and_relaunches(self, api, tmp_path, monkeypatch):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        release = updates.Release(
            tag="v99.0.0",
            assets={"NexusController.exe": updates.DOWNLOAD_PREFIX + "v99.0.0/NexusController.exe"},
        )
        started: list = []
        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.updates.fetch_latest", lambda **k: release)
        monkeypatch.setattr("nexus_server.updates.download_to", _writes(b"new build"))
        monkeypatch.setattr("nexus_server.updates.relaunch", lambda path, **k: started.append(path))

        result = api.install_update()

        assert result == {"ok": True, "restarting": True, "version": "99.0.0"}
        assert exe.read_bytes() == b"new build"
        assert started == [exe]
        assert api.update_status()["state"] == "installed"

    def test_a_failed_download_leaves_the_build_alone(self, api, tmp_path, monkeypatch):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        release = updates.Release(tag="v99.0.0", assets={})
        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.updates.fetch_latest", lambda **k: release)

        def refuse(*args, **kwargs):
            raise updates.UpdateError("does not match its checksum")

        monkeypatch.setattr("nexus_server.updates.download_to", refuse)
        result = api.install_update()

        assert result["ok"] is False
        assert exe.read_bytes() == b"old build"
        assert any("Update failed" in line for line in api.get_log())

        # The attempt failed; the release did not go anywhere. Dropping to
        # "error" took the offer off the page, and once the message was
        # dismissed there was no button left to try again with — and nothing
        # runs a check by itself, so that lasted until the app was restarted.
        status = api.update_status()
        assert status["state"] == "available"
        assert status["latest"] == "99.0.0"
        assert "checksum" in status["error"]

    def test_a_build_that_will_not_start_is_still_an_installed_build(
        self, api, tmp_path, monkeypatch
    ):
        """Failing to start it is a smaller problem with a different answer.

        Reported as a failed update it was worse than useless: the state went
        back to "available" and offered to install the version already on disk —
        and that second run would move the *new* build aside as the old one and
        throw away the only copy of what was there before.
        """
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        release = updates.Release(
            tag="v99.0.0",
            assets={"NexusController.exe": updates.DOWNLOAD_PREFIX + "v99.0.0/NexusController.exe"},
        )
        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.updates.writable", lambda path, **k: True)
        monkeypatch.setattr("nexus_server.updates.fetch_latest", lambda **k: release)
        monkeypatch.setattr("nexus_server.updates.download_to", _writes(b"new build"))

        def refuses(path, **kwargs):
            raise updates.UpdateError("the update is installed but could not be started")

        monkeypatch.setattr("nexus_server.updates.relaunch", refuses)
        result = api.install_update()

        assert result["ok"] is True
        assert result["restarting"] is False
        assert "start Nexus Controller again" in result["error"]
        assert exe.read_bytes() == b"new build"
        assert updates.backup_for(exe).read_bytes() == b"old build"
        assert api.update_status()["state"] == "installed"

    def test_once_installed_nothing_offers_to_install_it_again(
        self, api, tmp_path, monkeypatch
    ):
        """"installed" is where this process stops.

        The swap has happened, but *this* build is still the old one and still
        reports the old version — so a check would find the release newer than
        itself and offer it again, and a second install would move the new build
        aside as if it were the old one, throwing away the only copy of what was
        there before. The window stays open exactly when the new build could not
        be started, which is when a user is most likely to press the button
        again.
        """
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        release = updates.Release(
            tag="v99.0.0",
            assets={"NexusController.exe": updates.DOWNLOAD_PREFIX + "v99.0.0/NexusController.exe"},
        )
        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.updates.writable", lambda path, **k: True)
        monkeypatch.setattr("nexus_server.updates.fetch_latest", lambda **k: release)
        monkeypatch.setattr("nexus_server.updates.download_to", _writes(b"new build"))
        monkeypatch.setattr("nexus_server.updates.relaunch", lambda path, **k: None)
        assert api.install_update()["ok"] is True

        again = api.install_update()
        assert again["ok"] is False
        assert "already installed" in again["error"]
        assert api.check_for_update()["state"] == "installed"
        assert api.update_status()["state"] == "installed"
        assert updates.backup_for(exe).read_bytes() == b"old build"

    def test_a_dismissed_failure_does_not_come_back_with_the_offer(
        self, api, tmp_path, monkeypatch
    ):
        """The page keeps one copy of the sentence and the state keeps another."""
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        release = updates.Release(
            tag="v99.0.0",
            assets={"NexusController.exe": updates.DOWNLOAD_PREFIX + "v99.0.0/NexusController.exe"},
        )
        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.updates.writable", lambda path, **k: True)
        monkeypatch.setattr("nexus_server.updates.fetch_latest", lambda **k: release)

        def refuse(*args, **kwargs):
            raise updates.UpdateError("does not match its checksum")

        monkeypatch.setattr("nexus_server.updates.download_to", refuse)
        api.install_update()
        assert api.update_status()["error"]

        status = api.dismiss_update_error()
        assert status["error"] is None
        # And the offer is still standing, which is the point of keeping it.
        assert api.update_status()["state"] == "available"

    def test_an_install_that_never_found_a_release_has_no_offer_to_put_back(
        self, api, tmp_path, monkeypatch
    ):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.updates.writable", lambda path, **k: True)

        def offline(**kwargs):
            raise updates.UpdateError("could not reach GitHub")

        monkeypatch.setattr("nexus_server.updates.fetch_latest", offline)
        assert api.install_update()["ok"] is False
        assert api.update_status()["state"] == "error"

    def test_a_check_that_lands_during_an_install_does_not_take_the_state_back(
        self, api, tmp_path, monkeypatch
    ):
        """The check worker owns "checking" and nothing else.

        A check started a moment before the user pressed the button was still in
        flight when the install began, and wrote its answer over "installing" —
        which the page reads as "available", so the button came back to life
        under a download that was still running. A second install then raced the
        first through the rename in updates.install_staged(), where one moves the
        running .exe aside while the other is looking for it.
        """
        newer = updates.Release(
            tag="v99.0.0",
            assets={"NexusController.exe": updates.DOWNLOAD_PREFIX + "v99.0.0/NexusController.exe"},
        )
        entered, finish = threading.Event(), threading.Event()
        workers: list[threading.Thread] = []

        class Recorded(threading.Thread):
            def start(self):
                workers.append(self)
                super().start()

        def parked(**kwargs):
            entered.set()
            finish.wait(5)
            return newer

        api._thread_factory = Recorded
        monkeypatch.setattr("nexus_server.updates.fetch_latest", parked)
        api.check_for_update()
        assert entered.wait(5), "the check never started"

        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.updates.writable", lambda path, **k: True)
        monkeypatch.setattr("nexus_server.updates.download_to", _writes(b"new build"))
        monkeypatch.setattr("nexus_server.updates.relaunch", lambda path, **k: None)
        # The install does not ask GitHub through the parked function: it is
        # given its own answer, exactly as it would have on a real machine where
        # the two calls are separate requests.
        monkeypatch.setattr("nexus_server.updates.fetch_latest", lambda **k: newer)

        assert api.install_update()["ok"] is True
        assert api.update_status()["state"] == "installed"

        finish.set()                       # now let the check finish
        workers[0].join(5)
        assert not workers[0].is_alive(), "the check worker never finished"
        assert api.update_status()["state"] == "installed"

    def test_an_overtaken_check_does_not_answer_in_a_later_one_s_name(
        self, api, tmp_path, monkeypatch
    ):
        """"The state still says checking" is not the same question as "is this
        still the check that set it".

        A check parked on a slow network, an install, then a second check: by the
        time the first one comes back the state says "checking" again — for
        somebody else. Its answer is from before the install ran.
        """
        newer = updates.Release(
            tag="v99.0.0",
            assets={"NexusController.exe": updates.DOWNLOAD_PREFIX + "v99.0.0/NexusController.exe"},
        )
        workers: list[threading.Thread] = []

        class Recorded(threading.Thread):
            def start(self):
                workers.append(self)
                super().start()

        first_in, first_out = threading.Event(), threading.Event()
        second_in, second_out = threading.Event(), threading.Event()

        def parked_first(**kwargs):
            first_in.set()
            first_out.wait(5)
            return newer                       # "there is an update" — the stale answer

        def parked_second(**kwargs):
            second_in.set()
            second_out.wait(5)
            return None

        api._thread_factory = Recorded
        monkeypatch.setattr("nexus_server.updates.fetch_latest", parked_first)
        api.check_for_update()
        assert first_in.wait(5)

        # An install in between, which fails — so the state goes back to the
        # offer rather than to "installed", and a later check may run at all.
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.updates.writable", lambda path, **k: True)
        monkeypatch.setattr("nexus_server.updates.fetch_latest", lambda **k: newer)

        def refuse(*args, **kwargs):
            raise updates.UpdateError("does not match its checksum")

        monkeypatch.setattr("nexus_server.updates.download_to", refuse)
        assert api.install_update()["ok"] is False

        # The second check claims the state — and is still running when the first
        # one comes back, so "the state says checking" is true and says nothing.
        monkeypatch.setattr("nexus_server.updates.fetch_latest", parked_second)
        api.check_for_update()
        assert second_in.wait(5)

        first_out.set()
        workers[0].join(5)
        assert api.update_status()["state"] == "checking", "the stale answer was written"

        second_out.set()
        workers[1].join(5)
        assert api.update_status()["state"] == "none"

    def test_a_second_install_while_one_runs_is_refused(self, api, tmp_path, monkeypatch):
        """Two of these in the rename dance is the one way to end up with no .exe."""
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.updates.writable", lambda path, **k: True)
        newer = updates.Release(
            tag="v99.0.0",
            assets={"NexusController.exe": updates.DOWNLOAD_PREFIX + "v99.0.0/NexusController.exe"},
        )
        second: list[dict] = []

        def fetch_latest_and_try_again(**kwargs):
            # From inside the first install, which is what a second click on a
            # button the page re-enabled would do.
            second.append(api.install_update())
            return newer

        monkeypatch.setattr("nexus_server.updates.fetch_latest", fetch_latest_and_try_again)
        monkeypatch.setattr("nexus_server.updates.download_to", _writes(b"new build"))
        monkeypatch.setattr("nexus_server.updates.relaunch", lambda path, **k: None)

        assert api.install_update()["ok"] is True
        assert second[0]["ok"] is False
        assert "already being installed" in second[0]["error"]
        assert exe.read_bytes() == b"new build"

    def test_a_check_asked_for_during_an_install_is_not_started(self, api, tmp_path, monkeypatch):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        monkeypatch.setattr("nexus_server.updates.running_executable", lambda: exe)
        monkeypatch.setattr("nexus_server.updates.writable", lambda path, **k: True)
        newer = updates.Release(
            tag="v99.0.0",
            assets={"NexusController.exe": updates.DOWNLOAD_PREFIX + "v99.0.0/NexusController.exe"},
        )
        during: list[dict] = []

        def fetch_latest_and_check(**kwargs):
            during.append(api.check_for_update())
            return newer

        monkeypatch.setattr("nexus_server.updates.fetch_latest", fetch_latest_and_check)
        monkeypatch.setattr("nexus_server.updates.download_to", _writes(b"new build"))
        monkeypatch.setattr("nexus_server.updates.relaunch", lambda path, **k: None)

        assert api.install_update()["ok"] is True
        # The refusal answers with the whole status the page renders, not the
        # bare state dict — it is the same call the page would make next anyway.
        assert during[0]["state"] == "installing"
        assert "enabled" in during[0] and "can_install" in during[0]

    def test_the_check_can_be_turned_off_and_is_remembered(self, api, monkeypatch):
        monkeypatch.setattr("nexus_server.updates.fetch_latest", lambda **k: None)
        assert api.set_check_updates(False) is False
        assert api.store.load().check_updates is False
        assert api.update_status()["enabled"] is False
