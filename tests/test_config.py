"""Settings persistence."""

import json

import pytest

from nexus_server.config import Settings, SettingsStore, generate_token
from nexus_server.protocol import DEFAULT_TCP_PORT, DeviceType


@pytest.fixture()
def store(tmp_path):
    return SettingsStore(tmp_path / "settings.json")


class TestDefaults:
    def test_desktop_control_is_off_by_default(self):
        assert Settings().desktop_control is False

    def test_token_is_required_by_default(self):
        assert Settings().require_token is True

    def test_each_instance_gets_its_own_token(self):
        assert Settings().token != Settings().token

    def test_token_is_128_bits_of_hex(self):
        token = generate_token()
        assert len(token) == 32
        int(token, 16)


class TestRoundTrip:
    def test_save_and_load(self, store):
        original = Settings(haptics=False, pin_token=True, port=7000, theme="green")
        store.save(original)
        loaded = store.load()
        assert loaded.haptics is False
        assert loaded.port == 7000
        assert loaded.theme == "green"

    def test_pinned_token_survives_a_restart(self, store):
        original = Settings(pin_token=True, token="a" * 32)
        store.save(original)
        assert store.load().token == "a" * 32

    def test_unpinned_token_rotates_on_load(self, store):
        store.save(Settings(pin_token=False, token="a" * 32))
        assert store.load().token != "a" * 32

    def test_key_bindings_round_trip(self, store):
        store.save(Settings(pin_token=True, key_bindings={"0": {"a": "space"}}))
        assert store.load().key_bindings == {"0": {"a": "space"}}


class TestResilience:
    def test_missing_file_gives_defaults(self, store):
        assert store.load().port == DEFAULT_TCP_PORT

    def test_corrupt_json_gives_defaults(self, store):
        store.path.write_text("{not json at all", encoding="utf-8")
        assert store.load().port == DEFAULT_TCP_PORT

    def test_non_object_json_gives_defaults(self, store):
        store.path.write_text("[1, 2, 3]", encoding="utf-8")
        assert store.load().port == DEFAULT_TCP_PORT

    def test_unknown_keys_are_ignored(self, store):
        store.path.write_text(json.dumps({"port": 7001, "from_the_future": True}), encoding="utf-8")
        assert store.load().port == 7001

    def test_out_of_range_port_falls_back(self, store):
        store.path.write_text(json.dumps({"port": 999999}), encoding="utf-8")
        assert store.load().port == DEFAULT_TCP_PORT

    def test_wrong_type_port_falls_back(self, store):
        store.path.write_text(json.dumps({"port": "abc"}), encoding="utf-8")
        assert store.load().port == DEFAULT_TCP_PORT

    def test_invalid_device_type_falls_back(self, store):
        store.path.write_text(json.dumps({"default_device_type": 99}), encoding="utf-8")
        assert store.load().default_device_type == int(DeviceType.XBOX360)

    def test_desktop_slot_is_clamped(self, store):
        store.path.write_text(json.dumps({"desktop_slot": 99}), encoding="utf-8")
        assert store.load().desktop_slot == 3

    def test_short_token_is_regenerated(self, store):
        store.path.write_text(json.dumps({"pin_token": True, "token": "x"}), encoding="utf-8")
        assert len(store.load().token) == 32

    def test_bad_key_bindings_type_is_reset(self, store):
        store.path.write_text(json.dumps({"key_bindings": "nope"}), encoding="utf-8")
        assert store.load().key_bindings == {}


class TestAtomicWrite:
    def test_no_temporary_files_left_behind(self, store):
        store.save(Settings())
        assert list(store.path.parent.glob("*.tmp")) == []

    def test_existing_file_is_replaced_not_appended(self, store):
        store.save(Settings(port=7000))
        store.save(Settings(port=7001))
        assert json.loads(store.path.read_text(encoding="utf-8"))["port"] == 7001

    def test_saved_file_is_valid_json(self, store):
        store.save(Settings())
        json.loads(store.path.read_text(encoding="utf-8"))

    def test_creates_the_directory(self, tmp_path):
        store = SettingsStore(tmp_path / "nested" / "deep" / "settings.json")
        store.save(Settings())
        assert store.path.exists()
