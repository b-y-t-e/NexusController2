"""Persistent server settings.

Settings live in the user's roaming profile rather than next to the executable, so
the app keeps working when installed into ``Program Files`` (the original version
wrote ``settings.json`` into the working directory and silently lost every setting
when that directory was read-only).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .protocol import DEFAULT_DISCOVERY_PORT, DEFAULT_TCP_PORT, DeviceType

log = logging.getLogger(__name__)

APP_NAME = "NexusController"
TOKEN_BYTES = 16


def config_dir() -> Path:
    """Per-user configuration directory, created on demand."""
    base = os.environ.get("NEXUS_CONFIG_DIR")
    if base:
        path = Path(base)
    elif os.name == "nt":
        path = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_token() -> str:
    """A fresh 128-bit pairing token as 32 lowercase hex characters."""
    return secrets.token_hex(TOKEN_BYTES)


@dataclass
class Settings:
    #: Force feedback relayed back to the phone.
    haptics: bool = True
    #: Mouse/keyboard injection. Off by default — it is a remote-control capability.
    desktop_control: bool = False
    #: Slot allowed to drive the mouse and keyboard while ``desktop_control`` is on.
    desktop_slot: int = 0
    #: Require a matching pairing token in the handshake.
    require_token: bool = True
    #: Keep the same token across restarts instead of rotating it.
    pin_token: bool = False
    token: str = field(default_factory=generate_token)
    #: Interface to bind, or ``""`` for the auto-detected LAN address.
    bind_ip: str = ""
    port: int = DEFAULT_TCP_PORT
    discovery_port: int = DEFAULT_DISCOVERY_PORT
    #: Answer UDP discovery probes.
    discovery_enabled: bool = True
    #: Name broadcast in discovery replies; empty means "use the hostname".
    server_name: str = ""
    #: Try to add an inbound Windows Firewall rule on start (private profile only).
    manage_firewall: bool = True
    #: Set up ``adb reverse`` for USB mode on start.
    manage_adb: bool = True
    #: Controller type assigned to a client that does not request one.
    default_device_type: int = int(DeviceType.XBOX360)
    theme: str = "cyan"
    #: ``{"0": {"a": "space", ...}}`` — pad button → keyboard key, per slot.
    key_bindings: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Named pad layouts authored on the PC, ``{name: config document}``.
    #: This is what makes configuration central: author once, push to any phone.
    pad_profiles: dict[str, dict] = field(default_factory=dict)

    # -- validation ---------------------------------------------------------

    def normalized(self) -> "Settings":
        """Return a copy with every field forced into a sane range."""
        data = asdict(self)
        data["port"] = _clamp_port(self.port, DEFAULT_TCP_PORT)
        data["discovery_port"] = _clamp_port(self.discovery_port, DEFAULT_DISCOVERY_PORT)
        data["desktop_slot"] = max(0, min(3, int(self.desktop_slot)))
        try:
            data["default_device_type"] = int(DeviceType(self.default_device_type))
        except ValueError:
            data["default_device_type"] = int(DeviceType.XBOX360)
        if not isinstance(self.token, str) or len(self.token) < 8:
            data["token"] = generate_token()
        if not isinstance(self.key_bindings, dict):
            data["key_bindings"] = {}
        if not isinstance(self.pad_profiles, dict):
            data["pad_profiles"] = {}
        else:
            data["pad_profiles"] = {
                str(name)[:48]: value
                for name, value in self.pad_profiles.items()
                if isinstance(value, dict)
            }
        return Settings(**data)


def _clamp_port(value: Any, fallback: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return fallback
    return port if 1 <= port <= 65535 else fallback


class SettingsStore:
    """Loads and atomically saves :class:`Settings`."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "settings.json")

    def load(self) -> Settings:
        """Read settings, falling back to defaults for anything missing or corrupt."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Settings()
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("settings unreadable (%s); using defaults", exc)
            return Settings()
        if not isinstance(raw, dict):
            log.warning("settings file is not an object; using defaults")
            return Settings()
        known = {f.name for f in fields(Settings)}
        unknown = set(raw) - known
        if unknown:
            log.info("ignoring unknown settings keys: %s", ", ".join(sorted(unknown)))
        try:
            settings = Settings(**{k: v for k, v in raw.items() if k in known})
        except TypeError as exc:
            log.warning("settings have the wrong shape (%s); using defaults", exc)
            return Settings()
        settings = settings.normalized()
        if not settings.pin_token:
            settings.token = generate_token()
        return settings

    def save(self, settings: Settings) -> None:
        """Write settings atomically so a crash mid-write cannot corrupt them."""
        payload = json.dumps(asdict(settings.normalized()), indent=2, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except OSError:
            log.exception("could not save settings to %s", self.path)
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
