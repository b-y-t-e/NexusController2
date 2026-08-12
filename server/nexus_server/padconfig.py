"""Pad configuration documents — the thing the PC edits and pushes to a phone.

See ``docs/PROTOCOL.md`` §10 for the normative schema. Everything here is pure:
validation, clamping, merging and defaults, with no I/O, so the rules that decide
what a phone will look like are cheap to test exhaustively.

The central idea is that **positions are fractions of the screen, not pixels**.
A layout authored on the PC has to land in the same place on a 6" phone and a 11"
tablet, so ``x``/``y`` are 0.0–1.0 and address a component's *centre*.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final, Iterable

from .protocol import DeviceType, ProtocolError, sanitize_name

SCHEMA_VERSION: Final = 1
MAX_CONFIG_BYTES: Final = 16384

SCALE_MIN: Final = 0.5
SCALE_MAX: Final = 3.0
ROTATION_MIN: Final = -180.0
ROTATION_MAX: Final = 180.0


@dataclass(frozen=True, slots=True)
class Component:
    """A placeable control and how big it is at scale 1.0."""

    id: str
    label: str
    #: Nominal size as a fraction of screen *height*.
    size: float
    #: ``"round"`` a circle, ``"pad"`` a rounded square, ``"bar"`` an upright
    #: rounded bar three times as tall as it is wide — the Buzz answer button.
    shape: str = "round"


GAMEPAD_COMPONENTS: Final[tuple[Component, ...]] = (
    Component("L2", "L2", 0.15, "pad"),
    Component("L1", "L1", 0.13, "pad"),
    Component("DPAD", "D-pad", 0.42, "pad"),
    Component("L_STICK", "Left stick", 0.34),
    Component("R2", "R2", 0.15, "pad"),
    Component("R1", "R1", 0.13, "pad"),
    Component("FACE", "Face buttons", 0.42, "pad"),
    Component("R_STICK", "Right stick", 0.34),
    Component("SHARE", "Back / Share", 0.09),
    Component("OPTIONS", "Start / Options", 0.09),
    Component("PS", "Guide", 0.10),
)

BUZZ_COMPONENTS: Final[tuple[Component, ...]] = (
    Component("BUZZ_RED", "Buzz", 0.38),
    Component("BUZZ_BLUE", "Blue", 0.44, "bar"),
    Component("BUZZ_ORANGE", "Orange", 0.44, "bar"),
    Component("BUZZ_GREEN", "Green", 0.44, "bar"),
    Component("BUZZ_YELLOW", "Yellow", 0.44, "bar"),
)


def components_for(device_type: DeviceType) -> tuple[Component, ...]:
    """Xbox and DualShock share a component set; only the glyphs differ."""
    return BUZZ_COMPONENTS if device_type is DeviceType.BUZZ else GAMEPAD_COMPONENTS


def component_ids(device_type: DeviceType) -> set[str]:
    return {c.id for c in components_for(device_type)}


#: Sensible starting positions, landscape, centre-referenced fractions.
#: Kept identical to the Android client's defaults, so the preview shown for a
#: slot that has not reported yet matches what a phone would actually display.
DEFAULT_GAMEPAD_LAYOUT: Final[dict[str, dict[str, float]]] = {
    "L_STICK": {"x": 0.15, "y": 0.70, "s": 1.2},
    "R_STICK": {"x": 0.85, "y": 0.70, "s": 1.2},
    "DPAD":    {"x": 0.36, "y": 0.68},
    "FACE":    {"x": 0.64, "y": 0.68},
    "L1":      {"x": 0.10, "y": 0.17, "s": 0.9},
    "R1":      {"x": 0.90, "y": 0.17, "s": 0.9},
    "L2":      {"x": 0.10, "y": 0.34, "s": 0.9},
    "R2":      {"x": 0.90, "y": 0.34, "s": 0.9},
    "SHARE":   {"x": 0.42, "y": 0.30, "s": 0.9},
    "OPTIONS": {"x": 0.58, "y": 0.30, "s": 0.9},
    "PS":      {"x": 0.50, "y": 0.42},
}

#: The dome on the left, the four answer bars standing side by side beside it.
#: Mirrors LayoutStore.BUZZ_DEFAULTS.
#:
#: The buzzer stacks its bars flat, one above the other. On a screen that is wide
#: and short that arrangement wastes the dimension there is least of: a flat bar
#: can only be as long as a quarter of the width. Stood upright the bars are as
#: long as the screen is tall, and four of them still fit in a row.
DEFAULT_BUZZ_LAYOUT: Final[dict[str, dict[str, float]]] = {
    "BUZZ_RED":    {"x": 0.19, "y": 0.52, "s": 0.90},
    "BUZZ_BLUE":   {"x": 0.44, "y": 0.50, "s": 1.25},
    "BUZZ_ORANGE": {"x": 0.59, "y": 0.50, "s": 1.25},
    "BUZZ_GREEN":  {"x": 0.74, "y": 0.50, "s": 1.25},
    "BUZZ_YELLOW": {"x": 0.89, "y": 0.50, "s": 1.25},
}

DEFAULT_SETTINGS: Final[dict[str, Any]] = {
    "haptics": True,
    "hapticStrength": 0.85,
    "gyro": False,
    "gyroSensitivity": 0.4,
    "touchVibration": True,
    "theme": "Dark",
}


def _clamp(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:  # NaN
        return fallback
    return max(low, min(high, number))


def normalize_placement(raw: Any) -> dict[str, float]:
    """Clamp one component placement into legal ranges, filling in defaults."""
    if not isinstance(raw, dict):
        raw = {}
    return {
        "x": _clamp(raw.get("x"), 0.0, 1.0, 0.5),
        "y": _clamp(raw.get("y"), 0.0, 1.0, 0.5),
        "s": _clamp(raw.get("s", 1.0), SCALE_MIN, SCALE_MAX, 1.0),
        "r": _clamp(raw.get("r", 0.0), ROTATION_MIN, ROTATION_MAX, 0.0),
    }


def default_layout(device_type: DeviceType) -> dict[str, dict[str, float]]:
    source = DEFAULT_BUZZ_LAYOUT if device_type is DeviceType.BUZZ else DEFAULT_GAMEPAD_LAYOUT
    return {key: normalize_placement(value) for key, value in source.items()}


@dataclass
class PadConfig:
    """One phone's complete appearance and feel."""

    device_type: DeviceType = DeviceType.XBOX360
    name: str = ""
    layout: dict[str, dict[str, float]] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    screen: tuple[int, int] | None = None

    # -- construction -------------------------------------------------------

    @classmethod
    def default(cls, device_type: DeviceType = DeviceType.XBOX360, name: str = "") -> "PadConfig":
        return cls(device_type=device_type, name=name, layout=default_layout(device_type))

    @classmethod
    def from_dict(cls, raw: Any, *, strict: bool = False) -> "PadConfig":
        """Build from a decoded JSON object, dropping anything unusable.

        Unknown keys and unknown component IDs are ignored rather than fatal, so a
        newer phone talking to an older PC degrades instead of failing.
        """
        if not isinstance(raw, dict):
            raise ProtocolError("config must be a JSON object")
        version = raw.get("v")
        if version != SCHEMA_VERSION:
            raise ProtocolError(f"unsupported config schema version: {version!r}")

        device_type = _device_type_from_name(raw.get("type"))
        layout_raw = raw.get("layout")
        layout: dict[str, dict[str, float]] = {}
        if isinstance(layout_raw, dict):
            allowed = component_ids(device_type)
            for key, value in layout_raw.items():
                if key in allowed:
                    layout[key] = normalize_placement(value)
                elif strict:
                    raise ProtocolError(f"unknown component id {key!r}")

        settings = dict(DEFAULT_SETTINGS)
        if isinstance(raw.get("settings"), dict):
            settings.update(_clean_settings(raw["settings"]))

        screen = None
        screen_raw = raw.get("screen")
        if isinstance(screen_raw, dict):
            try:
                width, height = int(screen_raw.get("w", 0)), int(screen_raw.get("h", 0))
                if width > 0 and height > 0:
                    screen = (width, height)
            except (TypeError, ValueError):
                screen = None

        return cls(
            device_type=device_type,
            name=sanitize_name(str(raw.get("name", ""))),
            layout=layout,
            settings=settings,
            screen=screen,
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> "PadConfig":
        try:
            decoded = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProtocolError(f"config is not valid JSON: {exc}") from exc
        return cls.from_dict(decoded)

    # -- serialisation ------------------------------------------------------

    def to_dict(self, *, include_screen: bool = True) -> dict[str, Any]:
        document: dict[str, Any] = {
            "v": SCHEMA_VERSION,
            "type": self.device_type.name,
            "name": self.name,
            "layout": {key: dict(value) for key, value in self.layout.items()},
            "settings": dict(self.settings),
        }
        if include_screen and self.screen is not None:
            document["screen"] = {"w": self.screen[0], "h": self.screen[1]}
        return document

    def to_json(self, *, include_screen: bool = True) -> str:
        return json.dumps(self.to_dict(include_screen=include_screen), separators=(",", ":"))

    def encode_body(self) -> bytes:
        """UTF-8 body for a CONFIG / SET_CONFIG message, size-checked."""
        body = self.to_json(include_screen=False).encode("utf-8")
        if len(body) > MAX_CONFIG_BYTES:
            raise ProtocolError(f"config too large: {len(body)} bytes")
        return body

    # -- editing ------------------------------------------------------------

    def merged_with(self, patch: "PadConfig", *, layout: bool = True, settings: bool = True) -> "PadConfig":
        """Return a copy with parts of ``patch`` applied.

        A push that omits the layout must not wipe the layout, and vice versa —
        the dashboard sends partial documents all the time.
        """
        return PadConfig(
            device_type=patch.device_type,
            name=patch.name or self.name,
            layout={k: dict(v) for k, v in (patch.layout if layout else self.layout).items()},
            settings=dict(patch.settings if settings else self.settings),
            screen=self.screen,
        )

    def with_device_type(self, device_type: DeviceType) -> "PadConfig":
        """Switch type, keeping the layout when the component set is unchanged."""
        if components_for(device_type) == components_for(self.device_type):
            layout = {k: dict(v) for k, v in self.layout.items()}
        else:
            layout = default_layout(device_type)
        return PadConfig(
            device_type=device_type,
            name=self.name,
            layout=layout,
            settings=dict(self.settings),
            screen=self.screen,
        )

    def filled(self) -> "PadConfig":
        """Return a copy with every component of this type present."""
        layout = default_layout(self.device_type)
        layout.update({k: dict(v) for k, v in self.layout.items()})
        return PadConfig(
            device_type=self.device_type,
            name=self.name,
            layout=layout,
            settings=dict(self.settings),
            screen=self.screen,
        )

    @property
    def aspect(self) -> float:
        """Screen aspect ratio, defaulting to a common 20:9 phone in landscape."""
        if self.screen is None:
            return 20 / 9
        width, height = self.screen
        return max(width, height) / max(1, min(width, height))


def _device_type_from_name(value: Any) -> DeviceType:
    if isinstance(value, str):
        try:
            return DeviceType[value.strip().upper()]
        except KeyError:
            pass
    if isinstance(value, int):
        try:
            return DeviceType(value)
        except ValueError:
            pass
    return DeviceType.XBOX360


_BOOL_KEYS = ("haptics", "gyro", "touchVibration")
_UNIT_KEYS = ("hapticStrength", "gyroSensitivity")


def _clean_settings(raw: dict) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key in _BOOL_KEYS:
        if key in raw:
            cleaned[key] = bool(raw[key])
    for key in _UNIT_KEYS:
        if key in raw:
            cleaned[key] = _clamp(raw[key], 0.0, 1.0, float(DEFAULT_SETTINGS[key]))
    if isinstance(raw.get("theme"), str):
        cleaned["theme"] = raw["theme"][:24]
    return cleaned


def describe_components(device_type: DeviceType) -> list[dict[str, Any]]:
    """Component metadata for the dashboard editor."""
    return [
        {"id": c.id, "label": c.label, "size": c.size, "shape": c.shape}
        for c in components_for(device_type)
    ]
