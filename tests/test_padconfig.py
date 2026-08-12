"""Pad configuration documents — the rules behind central configuration."""

import json

import pytest

from nexus_server import padconfig
from nexus_server.padconfig import (
    DEFAULT_SETTINGS,
    MAX_CONFIG_BYTES,
    SCHEMA_VERSION,
    PadConfig,
    component_ids,
    components_for,
    default_layout,
    describe_components,
    normalize_placement,
)
from nexus_server.protocol import DeviceType, ProtocolError


class TestComponents:
    def test_xbox_and_dualshock_share_a_component_set(self):
        """Only the glyphs differ, so switching between them keeps the layout."""
        assert components_for(DeviceType.XBOX360) == components_for(DeviceType.DUALSHOCK4)

    def test_buzz_has_its_own_set(self):
        assert components_for(DeviceType.BUZZ) != components_for(DeviceType.XBOX360)
        assert component_ids(DeviceType.BUZZ) == {
            "BUZZ_RED", "BUZZ_BLUE", "BUZZ_ORANGE", "BUZZ_GREEN", "BUZZ_YELLOW"
        }

    def test_buzz_has_no_sticks_or_triggers(self):
        ids = component_ids(DeviceType.BUZZ)
        assert not ids & {"L_STICK", "R_STICK", "L2", "R2", "DPAD"}

    def test_the_buzzer_is_the_biggest_control(self):
        """By area, which is what a thumb cares about.

        Not by height any more: the answer bars stand upright, so they are the
        *longer* controls while being a third as wide.
        """
        sizes = {c.id: c.size for c in components_for(DeviceType.BUZZ)}
        dome = 3.14159 * (sizes["BUZZ_RED"] / 2) ** 2
        bar = max(sizes[i] for i in sizes if i != "BUZZ_RED") ** 2 / 3
        assert dome > bar

    def test_every_default_layout_entry_is_a_known_component(self):
        for device_type in DeviceType:
            assert set(default_layout(device_type)) <= component_ids(device_type)

    def test_defaults_cover_every_component(self):
        for device_type in DeviceType:
            assert set(default_layout(device_type)) == component_ids(device_type)

    def test_describe_components_shape(self):
        for entry in describe_components(DeviceType.XBOX360):
            assert set(entry) == {"id", "label", "size", "shape"}
            assert 0 < entry["size"] < 1


class TestNormalizePlacement:
    def test_defaults_for_an_empty_placement(self):
        assert normalize_placement({}) == {"x": 0.5, "y": 0.5, "s": 1.0, "r": 0.0}

    @pytest.mark.parametrize("value", [None, "nonsense", [], 42])
    def test_garbage_becomes_the_default(self, value):
        assert normalize_placement(value)["x"] == 0.5

    def test_position_is_clamped_to_the_screen(self):
        placed = normalize_placement({"x": 5.0, "y": -3.0})
        assert placed["x"] == 1.0 and placed["y"] == 0.0

    def test_scale_is_clamped(self):
        assert normalize_placement({"s": 99})["s"] == padconfig.SCALE_MAX
        assert normalize_placement({"s": 0.01})["s"] == padconfig.SCALE_MIN

    def test_rotation_is_clamped(self):
        assert normalize_placement({"r": 900})["r"] == 180.0
        assert normalize_placement({"r": -900})["r"] == -180.0

    def test_nan_falls_back(self):
        assert normalize_placement({"x": float("nan")})["x"] == 0.5

    def test_string_numbers_are_accepted(self):
        assert normalize_placement({"x": "0.25"})["x"] == 0.25


class TestRoundTrip:
    def test_default_document_round_trips(self):
        original = PadConfig.default(DeviceType.BUZZ, name="Ania")
        restored = PadConfig.from_json(original.to_json())
        assert restored.device_type is DeviceType.BUZZ
        assert restored.name == "Ania"
        assert restored.layout == original.layout

    @pytest.mark.parametrize("device_type", list(DeviceType))
    def test_every_device_type_round_trips(self, device_type):
        config = PadConfig.default(device_type)
        assert PadConfig.from_json(config.to_json()).device_type is device_type

    def test_type_is_written_as_a_name_not_a_number(self):
        assert json.loads(PadConfig.default(DeviceType.BUZZ).to_json())["type"] == "BUZZ"

    def test_schema_version_is_present(self):
        assert json.loads(PadConfig.default().to_json())["v"] == SCHEMA_VERSION

    def test_body_stays_well_under_the_limit(self):
        body = PadConfig.default(DeviceType.XBOX360).encode_body()
        assert 0 < len(body) < MAX_CONFIG_BYTES / 4

    def test_screen_is_omitted_from_the_wire_body(self):
        config = PadConfig.default()
        config.screen = (2400, 1080)
        assert "screen" not in json.loads(config.encode_body())
        assert "screen" in config.to_dict()


class TestParsing:
    def test_wrong_schema_version_is_rejected(self):
        with pytest.raises(ProtocolError, match="schema version"):
            PadConfig.from_dict({"v": 99, "type": "XBOX360"})

    def test_missing_version_is_rejected(self):
        with pytest.raises(ProtocolError):
            PadConfig.from_dict({"type": "XBOX360"})

    def test_non_object_is_rejected(self):
        with pytest.raises(ProtocolError, match="JSON object"):
            PadConfig.from_dict([1, 2, 3])

    def test_malformed_json_is_rejected(self):
        with pytest.raises(ProtocolError, match="not valid JSON"):
            PadConfig.from_json("{not json")

    def test_unknown_component_ids_are_dropped(self):
        config = PadConfig.from_dict({
            "v": 1, "type": "XBOX360",
            "layout": {"FACE": {"x": 0.5, "y": 0.5}, "NONSENSE": {"x": 0.1, "y": 0.1}},
        })
        assert "FACE" in config.layout
        assert "NONSENSE" not in config.layout

    def test_buzz_ids_are_dropped_from_a_gamepad_document(self):
        config = PadConfig.from_dict({
            "v": 1, "type": "XBOX360", "layout": {"BUZZ_RED": {"x": 0.5, "y": 0.5}},
        })
        assert config.layout == {}

    def test_unknown_top_level_keys_are_ignored(self):
        config = PadConfig.from_dict({"v": 1, "type": "BUZZ", "from_the_future": {"a": 1}})
        assert config.device_type is DeviceType.BUZZ

    def test_unknown_type_falls_back_to_xbox(self):
        assert PadConfig.from_dict({"v": 1, "type": "NINTENDO"}).device_type is DeviceType.XBOX360

    def test_numeric_type_is_accepted(self):
        assert PadConfig.from_dict({"v": 1, "type": 2}).device_type is DeviceType.BUZZ

    def test_name_is_sanitised(self):
        config = PadConfig.from_dict({"v": 1, "type": "XBOX360", "name": "a|b\nc"})
        assert "|" not in config.name and "\n" not in config.name

    def test_settings_are_merged_over_defaults(self):
        config = PadConfig.from_dict({"v": 1, "type": "XBOX360", "settings": {"haptics": False}})
        assert config.settings["haptics"] is False
        assert config.settings["gyroSensitivity"] == DEFAULT_SETTINGS["gyroSensitivity"]

    def test_settings_are_clamped(self):
        config = PadConfig.from_dict(
            {"v": 1, "type": "XBOX360", "settings": {"hapticStrength": 9, "gyroSensitivity": -4}}
        )
        assert config.settings["hapticStrength"] == 1.0
        assert config.settings["gyroSensitivity"] == 0.0

    def test_bad_settings_type_is_ignored(self):
        config = PadConfig.from_dict({"v": 1, "type": "XBOX360", "settings": "nope"})
        assert config.settings == DEFAULT_SETTINGS

    def test_screen_is_read_when_sane(self):
        config = PadConfig.from_dict(
            {"v": 1, "type": "XBOX360", "screen": {"w": 2400, "h": 1080}}
        )
        assert config.screen == (2400, 1080)
        assert config.aspect == pytest.approx(2400 / 1080)

    @pytest.mark.parametrize("screen", [{"w": 0, "h": 5}, {"w": -1, "h": -1}, {"w": "a", "h": "b"}, "x"])
    def test_bad_screen_is_ignored(self, screen):
        config = PadConfig.from_dict({"v": 1, "type": "XBOX360", "screen": screen})
        assert config.screen is None
        assert config.aspect == pytest.approx(20 / 9)


class TestEditing:
    def test_switching_between_gamepads_keeps_the_layout(self):
        original = PadConfig.default(DeviceType.XBOX360)
        original.layout["FACE"]["x"] = 0.123
        switched = original.with_device_type(DeviceType.DUALSHOCK4)
        assert switched.device_type is DeviceType.DUALSHOCK4
        assert switched.layout["FACE"]["x"] == 0.123

    def test_switching_to_buzz_resets_to_buzz_defaults(self):
        switched = PadConfig.default(DeviceType.XBOX360).with_device_type(DeviceType.BUZZ)
        assert set(switched.layout) == component_ids(DeviceType.BUZZ)

    def test_switching_back_from_buzz_restores_a_gamepad_layout(self):
        config = PadConfig.default(DeviceType.BUZZ).with_device_type(DeviceType.XBOX360)
        assert set(config.layout) == component_ids(DeviceType.XBOX360)

    def test_switching_type_does_not_mutate_the_original(self):
        original = PadConfig.default(DeviceType.XBOX360)
        before = dict(original.layout["FACE"])
        switched = original.with_device_type(DeviceType.DUALSHOCK4)
        switched.layout["FACE"]["x"] = 0.999
        assert original.layout["FACE"] == before

    def test_filled_adds_missing_components(self):
        sparse = PadConfig(device_type=DeviceType.XBOX360, layout={"FACE": normalize_placement({})})
        assert set(sparse.filled().layout) == component_ids(DeviceType.XBOX360)

    def test_filled_keeps_existing_positions(self):
        sparse = PadConfig(
            device_type=DeviceType.XBOX360, layout={"FACE": normalize_placement({"x": 0.11})}
        )
        assert sparse.filled().layout["FACE"]["x"] == 0.11

    def test_merge_can_change_only_the_layout(self):
        base = PadConfig.default()
        base.settings["haptics"] = False
        patch = PadConfig.default()
        patch.layout["FACE"]["x"] = 0.9
        merged = base.merged_with(patch, layout=True, settings=False)
        assert merged.layout["FACE"]["x"] == 0.9
        assert merged.settings["haptics"] is False

    def test_merge_can_change_only_the_settings(self):
        base = PadConfig.default()
        base.layout["FACE"]["x"] = 0.11
        patch = PadConfig.default()
        patch.settings["haptics"] = False
        merged = base.merged_with(patch, layout=False, settings=True)
        assert merged.layout["FACE"]["x"] == 0.11
        assert merged.settings["haptics"] is False

    def test_merge_keeps_the_existing_name_when_the_patch_has_none(self):
        base = PadConfig.default(name="Ania")
        assert base.merged_with(PadConfig.default(), layout=True).name == "Ania"


class TestScreenIndependence:
    """The reason coordinates are fractions rather than pixels."""

    def test_positions_are_fractions(self):
        for placement in default_layout(DeviceType.XBOX360).values():
            assert 0.0 <= placement["x"] <= 1.0
            assert 0.0 <= placement["y"] <= 1.0

    def test_a_layout_survives_being_moved_between_screen_sizes(self):
        authored = PadConfig.default(DeviceType.XBOX360)
        authored.screen = (2400, 1080)
        authored.layout["FACE"]["x"] = 0.8

        # Same document, different phone — nothing about the layout changes.
        received = PadConfig.from_json(authored.encode_body())
        received.screen = (1280, 720)
        assert received.layout["FACE"]["x"] == 0.8

    def test_default_layout_has_no_overlapping_centres(self):
        seen = set()
        for placement in default_layout(DeviceType.XBOX360).values():
            key = (round(placement["x"], 3), round(placement["y"], 3))
            assert key not in seen
            seen.add(key)


class TestBuzzDefaultLayout:
    """The hardware's shapes and order, arranged for a wide, short screen."""

    def _sizes(self):
        return {c.id: c.size for c in components_for(DeviceType.BUZZ)}

    def test_the_answer_buttons_are_bars_not_squares(self):
        """The real buzzer's answers are wide, flat bars, and the PC preview has
        to draw what the phone draws."""
        shapes = {c.id: c.shape for c in components_for(DeviceType.BUZZ)}
        assert shapes["BUZZ_RED"] == "round"
        for name in ("BUZZ_BLUE", "BUZZ_ORANGE", "BUZZ_GREEN", "BUZZ_YELLOW"):
            assert shapes[name] == "bar"

    def test_the_answers_stand_in_a_row_in_their_physical_order(self):
        layout = default_layout(DeviceType.BUZZ)
        answers = [n for n in layout if n != "BUZZ_RED"]
        assert {layout[n]["y"] for n in answers} == {0.50}, "the answers form one row"
        assert sorted(answers, key=lambda n: layout[n]["x"]) == [
            "BUZZ_BLUE", "BUZZ_ORANGE", "BUZZ_GREEN", "BUZZ_YELLOW"
        ]

    def test_the_dome_sits_beside_the_stack_not_above_it(self):
        layout = default_layout(DeviceType.BUZZ)
        assert layout["BUZZ_RED"]["x"] < layout["BUZZ_BLUE"]["x"]

    def test_nothing_runs_off_the_top_or_bottom(self):
        sizes = self._sizes()
        for name, placement in default_layout(DeviceType.BUZZ).items():
            half = sizes[name] * placement["s"] / 2
            assert placement["y"] - half >= 0.0, f"{name} runs off the top"
            assert placement["y"] + half <= 1.0, f"{name} runs off the bottom"

    def test_the_row_does_not_overlap_itself(self):
        sizes = self._sizes()
        layout = default_layout(DeviceType.BUZZ)
        answers = sorted(
            (n for n in layout if n != "BUZZ_RED"), key=lambda n: layout[n]["x"]
        )
        for aspect in self.ASPECTS:
            for left, right in zip(answers, answers[1:]):
                gap = layout[right]["x"] - layout[left]["x"]
                needed = (
                    sizes[left] * layout[left]["s"] * self.BAR_ASPECT
                    + sizes[right] * layout[right]["s"] * self.BAR_ASPECT
                ) / 2 / aspect
                assert gap >= needed, f"{left} and {right} overlap at {aspect:.2f}"

    #: Landscape shapes the layout has to survive: 4:3 tablet to 21:9 phone.
    ASPECTS = (4 / 3, 16 / 9, 21 / 9)
    #: An upright answer bar is a third as wide as it is tall — see pad.js.
    BAR_ASPECT = 1 / 3

    def test_it_fits_every_landscape_shape(self):
        """Sizes are fractions of height, so how wide a control is depends on the
        screen. A 4:3 tablet is the narrowest case and used to clip the dome."""
        sizes = self._sizes()
        layout = default_layout(DeviceType.BUZZ)
        for aspect in self.ASPECTS:
            for name, placement in layout.items():
                width = sizes[name] * placement["s"]
                if name != "BUZZ_RED":
                    width *= self.BAR_ASPECT  # an upright bar is a third as wide as tall
                half = width / aspect / 2
                assert placement["x"] - half >= 0.0, f"{name} clips left at {aspect:.2f}"
                assert placement["x"] + half <= 1.0, f"{name} clips right at {aspect:.2f}"

    def test_the_dome_never_reaches_the_stack(self):
        sizes = self._sizes()
        layout = default_layout(DeviceType.BUZZ)
        for aspect in self.ASPECTS:
            dome = sizes["BUZZ_RED"] * layout["BUZZ_RED"]["s"] / aspect / 2
            bar = sizes["BUZZ_BLUE"] * layout["BUZZ_BLUE"]["s"] * self.BAR_ASPECT / aspect / 2
            assert (
                layout["BUZZ_RED"]["x"] + dome <= layout["BUZZ_BLUE"]["x"] - bar
            ), f"the dome overlaps the bars at {aspect:.2f}"

    def test_the_controls_are_worth_hitting(self):
        """The point of the arrangement: room to be big, not a tenth of the screen."""
        sizes = self._sizes()
        layout = default_layout(DeviceType.BUZZ)
        assert sizes["BUZZ_RED"] * layout["BUZZ_RED"]["s"] >= 0.33
        for name in ("BUZZ_BLUE", "BUZZ_ORANGE", "BUZZ_GREEN", "BUZZ_YELLOW"):
            assert sizes[name] * layout[name]["s"] >= 0.45
