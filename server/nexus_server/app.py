"""Desktop dashboard and command-line entry point.

The UI is a small local web page rendered by pywebview. Every asset it needs is
shipped inside the package — no CDN, no web fonts, no remote scripts. The old
dashboard pulled Tailwind and Chart.js from the internet, which meant the whole
app was dead on a machine with no connection (an odd requirement for something
whose entire job is local networking) and gave third-party scripts a bridge into
Python.
"""

from __future__ import annotations

import argparse
import base64
import io
import logging
import os
import sys
import threading
import time
from pathlib import Path

from . import __version__, updates
from .config import Settings, SettingsStore, generate_token
from .desktop import DesktopControl, create_backend
from .devices import DriverUnavailableError, FakeBackend, PadBackend, VGamepadBackend
from .netinfo import local_ips
from .padconfig import PadConfig, describe_components
from .protocol import DeviceType, ProtocolError, valid_ipv4
from .server import ControllerServer

log = logging.getLogger(__name__)

def _resource_dir() -> Path:
    """Where bundled data lives, both from source and inside a frozen build.

    PyInstaller unpacks a one-file build into a temporary directory and points
    ``sys._MEIPASS`` at it, so paths relative to ``__file__`` are not enough.
    """
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).parent


WEB_DIR = _resource_dir() / "web" if getattr(sys, "frozen", False) else Path(__file__).parent / "web"
#: ViGEmBus installer bundled at build time, if the build machine fetched it.
#: Current releases ship a self-contained .exe; older ones shipped an .msi.
_DRIVER_CANDIDATES = ("ViGEmBusSetup.exe", "ViGEmBusSetup.msi")


def _bundled_driver() -> Path | None:
    vendor = _resource_dir() / "vendor"
    for name in _DRIVER_CANDIDATES:
        candidate = vendor / name
        if candidate.is_file():
            return candidate
    return None
MAX_LOG_LINES = 200
DRIVER_HELP_URL = "https://github.com/nefarius/ViGEmBus/releases/latest"
#: How long a firewall check is reused before ``netsh`` is asked again.
FIREWALL_CACHE_SECONDS = 15.0
#: How long to wait for the elevated command to actually add the rules.
FIREWALL_VERIFY_SECONDS = 8.0


class Api:
    """The bridge exposed to the dashboard page as ``window.pywebview.api``."""

    def __init__(self, backend: PadBackend, *, simulated: bool = False) -> None:
        self.store = SettingsStore()
        self.settings = self.store.load()
        self.simulated = simulated
        self.desktop = DesktopControl(
            create_backend(),
            enabled=self.settings.desktop_control,
            slot=self.settings.desktop_slot,
        )
        self._log_lines: list[str] = []
        self._log_lock = threading.Lock()
        self.server = ControllerServer(
            self.settings, backend, self.desktop, log_sink=self._append_log
        )
        self._qr_cache: tuple[str, str] | None = None
        #: ``(checked_at, rules_present, public_networks)`` — see :meth:`firewall_status`.
        self._firewall_cache: tuple[float, bool | None, list[str]] | None = None
        #: Third-party firewalls, looked up once — they do not come and go.
        self._other_firewalls: list[str] | None = None
        self._pps_last_total = 0
        self._pps_last_time = time.monotonic()
        self._pps = 0
        #: What the dashboard is told about updates; replaced wholesale, never
        #: mutated in place, so the UI thread reading it never sees a half-written
        #: answer while the checking thread writes one.
        self._update: dict = {"state": "idle", "current": __version__}
        self._update_lock = threading.Lock()

    # -- logging ------------------------------------------------------------

    def _append_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        with self._log_lock:
            self._log_lines.append(f"{stamp}  {message}")
            del self._log_lines[:-MAX_LOG_LINES]

    def get_log(self) -> list[str]:
        with self._log_lock:
            return list(self._log_lines)

    # -- state --------------------------------------------------------------

    def _packets_per_second(self) -> int:
        now = time.monotonic()
        elapsed = now - self._pps_last_time
        if elapsed >= 0.5:
            total = self.server.total_packets
            self._pps = int((total - self._pps_last_total) / elapsed)
            self._pps_last_total = total
            self._pps_last_time = now
        return self._pps

    def get_state(self) -> dict:
        state = self.server.snapshot()
        state.update(
            {
                "pps": self._packets_per_second() if state["running"] else 0,
                "qr": self._qr_image() if state["running"] else None,
                # Guarded: pairing_payload validates the address and the token,
                # and the dashboard polls this five times a second. One bad field
                # must dim the QR code, not take the whole window down.
                "pairing": self._pairing_payload_or_empty() if state["running"] else "",
                "token": self.settings.token if self.settings.require_token else "",
                "ips": local_ips(),
                "bind_ip": self.settings.bind_ip,
                "theme": self.settings.theme,
                "simulated": self.simulated,
                "desktop_slot": self.desktop.slot,
                "pin_token": self.settings.pin_token,
                "version": __version__,
                "log": self.get_log(),
                "device_types": [
                    {"value": int(t), "label": t.label, "name": t.name} for t in DeviceType
                ],
                # Static geometry tables, sent once per poll so the dashboard can
                # draw any pad without a second round trip.
                "component_sets": {
                    t.name: describe_components(t) for t in DeviceType
                },
            }
        )
        return state

    def _pairing_payload_or_empty(self) -> str:
        try:
            return self.server.pairing_payload
        except ProtocolError as exc:
            log.warning("cannot build the pairing payload: %s", exc)
            return ""

    def _qr_image(self) -> str | None:
        payload = self._pairing_payload_or_empty()
        if not payload:
            return None
        if self._qr_cache and self._qr_cache[0] == payload:
            return self._qr_cache[1]
        try:
            import qrcode  # noqa: PLC0415

            code = qrcode.QRCode(box_size=8, border=2)
            code.add_data(payload)
            code.make(fit=True)
            image = code.make_image(fill_color="#0b0f14", back_color="white")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:  # noqa: BLE001
            log.exception("could not render the pairing QR code")
            return None
        self._qr_cache = (payload, encoded)
        return encoded

    # -- server control -----------------------------------------------------

    def start_server(self, bind_ip: str | None = None) -> dict:
        if bind_ip and bind_ip != "AUTO":
            # Checked here, where it can still be refused with a message, rather
            # than at the first poll where it would only produce a broken QR.
            if not valid_ipv4(bind_ip):
                return {"ok": False, "error": f"{bind_ip} is not an IPv4 address"}
            self.settings.bind_ip = bind_ip
        elif bind_ip == "AUTO":
            self.settings.bind_ip = ""
        try:
            self.server.start()
        except OSError as exc:
            self._append_log(f"Start failed: {exc}")
            return {"ok": False, "error": str(exc)}
        self.store.save(self.settings)
        return {"ok": True}

    def stop_server(self) -> dict:
        self.server.stop()
        return {"ok": True}

    # -- settings -----------------------------------------------------------

    def _persist(self) -> None:
        self.store.save(self.settings)

    def set_haptics(self, enabled: bool) -> bool:
        self.settings.haptics = bool(enabled)
        self._persist()
        return self.settings.haptics

    def set_desktop_control(self, enabled: bool) -> dict:
        """Enable or disable remote mouse/keyboard control."""
        if enabled and not self.desktop.available:
            return {"ok": False, "error": "pynput is not installed"}
        self.settings.desktop_control = bool(enabled)
        # Gate first, release second — see set_desktop_slot.
        self.desktop.enabled = self.settings.desktop_control
        if not enabled:
            self.server.reset_desktop_state()
        self._persist()
        self._append_log(
            "Desktop control ENABLED — connected phones can move the mouse and type"
            if enabled
            else "Desktop control disabled"
        )
        return {"ok": True, "enabled": self.settings.desktop_control}

    def set_desktop_slot(self, slot: int) -> int:
        slot = max(0, min(len(self.server.slots) - 1, int(slot)))
        changed = slot != self.desktop.slot
        self.settings.desktop_slot = slot
        # Shut the gate *first*, then release. The other order leaves a window in
        # which an input frame from the old slot is still allowed through and can
        # press a key that the reset has already accounted for as released.
        self.desktop.slot = slot
        if changed:
            # The old slot loses the lock this instant and can no longer send a
            # release, so whatever it was holding has to be let go here.
            self.server.reset_desktop_state()
        self._persist()
        return slot

    def set_theme(self, theme: str) -> str:
        if isinstance(theme, str) and theme:
            self.settings.theme = theme[:24]
            self._persist()
        return self.settings.theme

    def set_pin_token(self, pinned: bool) -> bool:
        self.settings.pin_token = bool(pinned)
        self._persist()
        return self.settings.pin_token

    def regenerate_token(self) -> str:
        self.settings.token = generate_token()
        self._qr_cache = None
        self._persist()
        self._append_log("Pairing token regenerated — reconnect your phones")
        return self.settings.token

    def set_require_token(self, required: bool) -> bool:
        self.settings.require_token = bool(required)
        self._qr_cache = None
        self._persist()
        if not required:
            self._append_log("WARNING: pairing token disabled — anyone on this network can connect")
        return self.settings.require_token

    # -- key bindings -------------------------------------------------------

    def get_bindings(self, slot: int) -> dict:
        return dict(self.settings.key_bindings.get(str(int(slot)), {}))

    def set_key_bind(self, slot: int, button: str, key: str) -> dict:
        from .desktop import BUTTON_BITS  # noqa: PLC0415

        slot = int(slot)
        button = str(button).lower()
        if button not in BUTTON_BITS:
            return {"ok": False, "error": f"unknown button {button!r}"}
        bindings = self.settings.key_bindings.setdefault(str(slot), {})
        if key:
            bindings[button] = str(key)[:24]
        else:
            bindings.pop(button, None)
        self._apply_bindings(slot)
        self._persist()
        return {"ok": True, "bindings": dict(bindings)}

    def clear_bindings(self, slot: int) -> dict:
        self.settings.key_bindings.pop(str(int(slot)), None)
        self._apply_bindings(int(slot))
        self._persist()
        return {"ok": True}

    def _apply_bindings(self, slot: int) -> None:
        if 0 <= slot < len(self.server.slots):
            stranded = self.server.slots.sessions[slot].keys.set_bindings(
                self.settings.key_bindings.get(str(slot), {})
            )
            # Whatever the old bindings were holding down has just lost the only
            # thing that could have released it.
            for key, pressed in stranded:
                self.desktop.set_key(slot, key, pressed)

    # -- central pad configuration -----------------------------------------

    def get_pad_config(self, slot: int) -> dict:
        """What this phone currently looks like, plus everything the editor needs."""
        slot = int(slot)
        session = (
            self.server.slots.sessions[slot]
            if 0 <= slot < len(self.server.slots)
            else None
        )
        config = session.config if session and session.config else None
        if config is None:
            config = PadConfig.default(DeviceType(self.settings.default_device_type))
        return {
            "slot": slot,
            "connected": bool(session and session.connected),
            "reported": session.config is not None if session else False,
            "config": config.to_dict(),
            "aspect": round(config.aspect, 4),
            "components": describe_components(config.device_type),
            "profiles": sorted(self.settings.pad_profiles),
        }

    def get_components(self, device_type: str) -> list:
        """Component metadata for a controller type, for the editor canvas."""
        return describe_components(_device_type(device_type))

    def push_pad_config(self, slot: int, document: dict) -> dict:
        """Send an edited configuration to one phone."""
        try:
            config = PadConfig.from_dict(document)
        except ProtocolError as exc:
            return {"ok": False, "error": str(exc)}
        if not self.server.push_config(int(slot), config):
            return {"ok": False, "error": "Player not connected"}
        return {"ok": True}

    def push_pad_config_to_all(self, document: dict) -> dict:
        """Send one configuration to every connected phone."""
        try:
            config = PadConfig.from_dict(document)
        except ProtocolError as exc:
            return {"ok": False, "error": str(exc)}
        sent = sum(
            1
            for session in self.server.slots.sessions
            if session.connected and self.server.push_config(session.index, config)
        )
        if not sent:
            return {"ok": False, "error": "No phone is connected"}
        return {"ok": True, "sent": sent}

    def set_pad_device_type(self, slot: int, device_type: str) -> dict:
        """Change a phone's emulated controller type from the PC.

        The layout is kept when the component set is the same (Xbox ↔ DualShock 4)
        and reset to sensible defaults when it is not (either ↔ Buzz).
        """
        slot = int(slot)
        if not 0 <= slot < len(self.server.slots):
            return {"ok": False, "error": "No such slot"}
        session = self.server.slots.sessions[slot]
        current = session.config or PadConfig.default()
        updated = current.with_device_type(_device_type(device_type))
        if not self.server.push_config(slot, updated):
            return {"ok": False, "error": "Player not connected"}
        return {"ok": True, "config": updated.to_dict()}

    def reset_pad_layout(self, slot: int) -> dict:
        """Put a phone back to the default layout for its current type."""
        slot = int(slot)
        if not 0 <= slot < len(self.server.slots):
            return {"ok": False, "error": "No such slot"}
        session = self.server.slots.sessions[slot]
        device_type = session.config.device_type if session.config else DeviceType.XBOX360
        config = PadConfig.default(device_type, name=session.name)
        if not self.server.push_config(slot, config):
            return {"ok": False, "error": "Player not connected"}
        return {"ok": True, "config": config.to_dict()}

    # -- profile library ----------------------------------------------------

    def list_profiles(self) -> list:
        return sorted(self.settings.pad_profiles)

    def save_profile(self, name: str, document: dict) -> dict:
        name = str(name).strip()[:48]
        if not name:
            return {"ok": False, "error": "Profile needs a name"}
        try:
            config = PadConfig.from_dict(document)
        except ProtocolError as exc:
            return {"ok": False, "error": str(exc)}
        self.settings.pad_profiles[name] = config.to_dict(include_screen=False)
        self._persist()
        self._append_log(f"Profile saved: {name}")
        return {"ok": True, "profiles": sorted(self.settings.pad_profiles)}

    def load_profile(self, name: str) -> dict:
        document = self.settings.pad_profiles.get(str(name))
        if document is None:
            return {"ok": False, "error": "No such profile"}
        return {"ok": True, "config": document}

    def delete_profile(self, name: str) -> dict:
        self.settings.pad_profiles.pop(str(name), None)
        self._persist()
        return {"ok": True, "profiles": sorted(self.settings.pad_profiles)}

    def apply_profile(self, name: str, slot: int = -1) -> dict:
        """Push a saved profile to one slot, or to every connected phone.

        This is the point of the whole feature: author a layout once on the PC and
        put it on every device without touching any of them.
        """
        document = self.settings.pad_profiles.get(str(name))
        if document is None:
            return {"ok": False, "error": "No such profile"}
        if int(slot) >= 0:
            return self.push_pad_config(int(slot), document)
        return self.push_pad_config_to_all(document)

    def test_rumble(self, slot: int, strength: float = 0.6) -> bool:
        slot = int(slot)
        if not 0 <= slot < len(self.server.slots):
            return False
        session = self.server.slots.sessions[slot]
        if not session.connected:
            return False
        magnitude = max(0, min(255, int(float(strength) * 255)))
        from .protocol import encode_rumble  # noqa: PLC0415

        return session.send(encode_rumble(magnitude, magnitude))

    def _serving_profile(self, system, categories: dict[str, str]) -> str:
        """Which firewall profile covers the interface the server serves on.

        The bound address decides it. "Is any network public?" is a different and
        usually wrong question — a VPN adapter or a second Wi-Fi would drag the
        answer to "public" on a machine serving a private LAN, and then a
        perfectly good rule reads as missing.
        """
        category = system.network_category_for(self.server.bind_ip or "")
        if category is None:
            # Unknown: fall back to the cautious reading, so a public network we
            # failed to identify is not silently treated as covered. Uses the
            # categories already read for this cache entry rather than asking
            # PowerShell all over again.
            if any(c.lower() == "public" for c in categories.values()):
                return "public"
            return "private"
        return "public" if category.lower() == "public" else "private"

    def firewall_status(self) -> dict:
        """Whether the LAN ports are open, and whether we can offer to open them.

        Cached: answering means shelling out to ``netsh`` twice, which is far too
        expensive for the dashboard's poll loop, and the answer only changes when
        somebody changes it.
        """
        from . import system  # noqa: PLC0415

        now = time.monotonic()
        if self._firewall_cache is None or now - self._firewall_cache[0] > FIREWALL_CACHE_SECONDS:
            # Everything expensive goes inside the cache, not just the netsh
            # calls: reading the network category spawns a PowerShell, which is
            # dearer than both of them and was running on every single poll.
            categories = system.active_network_categories()
            public_now = self._serving_profile(system, categories) == "public"
            self._firewall_cache = (
                now,
                # Asked about the profile the *current* network actually uses.
                # Answering "open" from a private-profile rule while sitting on a
                # public network is how the banner used to disappear on a port
                # that was still shut.
                system.firewall_rules_present(
                    self.settings.port,
                    self.settings.discovery_port,
                    "public" if public_now else "private",
                ),
                # The rules are scoped to the private profile, so a network
                # Windows has filed under Public is not covered by them at all —
                # the commonest way for every rule to be in place and the port to
                # still be shut.
                sorted(
                    name
                    for name, category in categories.items()
                    if category.lower() == "public"
                ),
            )
        if self._other_firewalls is None:
            self._other_firewalls = system.third_party_firewalls()
        return {
            "open": self._firewall_cache[1],
            "windows": system.is_windows(),
            "admin": system.is_admin(),
            "tcp": self.settings.port,
            "udp": self.settings.discovery_port,
            # USB never needs a rule: the phone dials 127.0.0.1 through adb reverse.
            "needed_for": "Wi-Fi",
            "others": self._other_firewalls,
            "public_networks": self._firewall_cache[2],
        }

    def open_firewall(self, include_public: bool = False) -> dict:
        """Offer to open the ports, elevating through UAC if we are not admin.

        Waits for the result instead of reporting the launch as a success.
        ``ShellExecute`` returns the moment the elevated process starts, so
        "opening…" is not an outcome — and when nothing appears afterwards the
        user is left pressing a button that seems to do nothing.
        """
        from . import system  # noqa: PLC0415

        result = system.open_firewall_elevated(
            self.settings.port, self.settings.discovery_port, include_public=bool(include_public)
        )
        self._firewall_cache = None      # whatever it was, re-read it next time
        if not result.ok:
            self._append_log(result.message)
            return {"ok": False, "message": result.message}

        profile = self._serving_profile(system, system.active_network_categories())

        deadline = time.monotonic() + FIREWALL_VERIFY_SECONDS
        present: bool | None = False
        while time.monotonic() < deadline:
            present = system.firewall_rules_present(
                self.settings.port, self.settings.discovery_port, profile
            )
            if present is not False:
                break
            time.sleep(0.4)

        others = self._other_firewalls or system.third_party_firewalls()
        if present:
            message = f"Ports open: TCP {self.settings.port}, UDP {self.settings.discovery_port}"
            if others:
                message += (
                    f". Note: {', '.join(others)} filters traffic as well, so you may "
                    "also have to allow the app there"
                )
        elif system.firewall_rules_present(
            self.settings.port,
            self.settings.discovery_port,
            "private" if profile == "public" else "public",
        ):
            # The commonest outcome of all, and the one the old message got
            # flatly wrong: the command ran, the rules are there, they just do
            # not cover the network this PC is actually on.
            other = "private" if profile == "public" else "public"
            advice = (
                "Press “Include public networks”, or set that network to private "
                "in Windows settings."
                if profile == "public"
                else "Remove the rules and add them again from here."
            )
            message = (
                f"The rules were added, but only for {other} networks — and Windows "
                f"lists the network this PC is on as {profile}. {advice}"
            )
        elif present is None:
            # Three outcomes, not two. Reporting "the rules were not added" when
            # the truth is "netsh could not be asked" sends the user hunting for
            # a problem that may not exist.
            message = (
                "Windows accepted the command, but the rules could not be read back — "
                f"check TCP {self.settings.port} and UDP {self.settings.discovery_port} "
                "in Windows Defender Firewall yourself"
            )
        else:
            message = (
                "The rules were not added — the elevated command did not run. "
                "Run tools\\add_firewall_rule.bat as Administrator to see why"
            )
            if others:
                message += f", and check whether {', '.join(others)} blocked it"
        self._append_log(message)
        return {"ok": present is True, "unknown": present is None, "message": message}

    def driver_status(self) -> dict:
        """Whether ViGEmBus is usable, and whether we can install it ourselves."""
        return {
            "installed": not self.simulated,
            "bundled": _bundled_driver() is not None,
            "url": DRIVER_HELP_URL,
        }

    def install_driver(self) -> dict:
        """Launch the bundled ViGEmBus installer, or fall back to the download page.

        Installing a kernel driver needs elevation, so Windows shows its own UAC
        prompt — we deliberately do not try to work around that.
        """
        installer = _bundled_driver()
        if installer is None:
            self.open_driver_page()
            return {"ok": True, "launched": False, "message": "Opened the download page"}
        command = (
            ["msiexec", "/i", str(installer)]
            if installer.suffix.lower() == ".msi"
            else [str(installer)]
        )
        try:
            import subprocess  # noqa: PLC0415

            subprocess.Popen(command)  # noqa: S603 - fixed path from our own bundle
        except OSError as exc:
            self._append_log(f"Could not start the driver installer: {exc}")
            return {"ok": False, "error": str(exc)}
        self._append_log("ViGEmBus installer started — reboot afterwards, then restart this app")
        return {"ok": True, "launched": True, "message": "Installer started"}

    def open_driver_page(self) -> None:
        import webbrowser  # noqa: PLC0415

        webbrowser.open(DRIVER_HELP_URL)

    # -- updates ------------------------------------------------------------

    def _set_update(self, **fields) -> dict:
        """Replace the update state, keeping the parts nobody is changing."""
        with self._update_lock:
            state = dict(self._update)
            state.update(fields)
            state["current"] = __version__
            self._update = state
            return dict(state)

    def update_status(self) -> dict:
        """What the dashboard shows. Never blocks — the work happens elsewhere."""
        with self._update_lock:
            state = dict(self._update)
        exe = updates.running_executable()
        state.update(
            {
                "enabled": self.settings.check_updates,
                # Installing needs a real .exe in a directory we may write to. From
                # a source checkout there is nothing to replace, and under Program
                # Files the swap needs rights this process does not have — in both
                # cases the honest answer is a link, not a button that fails.
                "can_install": exe is not None and updates.writable(exe),
                "url": updates.RELEASES_PAGE,
            }
        )
        return state

    def check_for_update(self) -> dict:
        """Ask GitHub, in the background. The answer arrives via :meth:`update_status`."""
        with self._update_lock:
            if self._update.get("state") in {"checking", "installing"}:
                return dict(self._update)
            self._update = {**self._update, "state": "checking", "error": None}

        def work() -> None:
            try:
                release = updates.fetch_latest()
            except updates.UpdateError as exc:
                # A failed check is not news. It happens on every start on a
                # machine with no route to the internet, which is a machine this
                # app is expressly meant to work on, so it goes to the state the
                # dashboard can choose to show and not into the log or a dialog.
                log.info("update check failed: %s", exc)
                self._set_update(state="error", error=str(exc))
                return
            except Exception as exc:  # noqa: BLE001 - see below
                # Anything else is a bug in here, not a network problem — but the
                # state machine must not care which it was. "checking" is a state
                # nothing else can leave, and check_for_update() refuses to start
                # while it is set, so an escaping exception would jam every later
                # check for the life of the process. On a daemon thread nobody
                # would even see the traceback.
                log.exception("update check raised")
                self._set_update(state="error", error=f"the update check failed: {exc}")
                return
            if release is None:
                self._set_update(state="none", latest=None, error=None)
                return
            available = updates.is_newer(__version__, release.version)
            self._set_update(
                state="available" if available else "none",
                latest=release.version,
                tag=release.tag,
                error=None,
                has_asset=release.url(updates.ASSET_NAME) is not None,
            )

        threading.Thread(target=work, name="update-check", daemon=True).start()
        return self.update_status()

    def install_update(self) -> dict:
        """Download the new build, put it in place, start it and ask to be closed.

        Runs to completion before answering: the dashboard's button is disabled
        while it does, and a download that the user cannot see the end of is worse
        than one that takes a moment.
        """
        exe = updates.running_executable()
        if exe is None:
            return {"ok": False, "error": "This is a source checkout — update it with git"}
        if not updates.writable(exe):
            return {
                "ok": False,
                "error": f"No permission to replace {exe.name} — download it from the releases page",
            }

        self._set_update(state="installing", error=None)
        try:
            release = updates.fetch_latest()
            if release is None or not updates.is_newer(__version__, release.version):
                self._set_update(state="none")
                return {"ok": False, "error": "There is no newer release"}
            payload = updates.download(release)
            updates.install(payload, exe)
            updates.relaunch(exe)
        except updates.UpdateError as exc:
            self._append_log(f"Update failed: {exc}")
            self._set_update(state="error", error=str(exc))
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - the state must be left usable
            # Same reasoning as the check, and worse: "installing" disables the
            # button *and* blocks every check, so a bug here would take the whole
            # feature out until the app is restarted. The dashboard is told in
            # the same shape as any other failure.
            log.exception("update install raised")
            message = f"the update failed unexpectedly: {exc}"
            self._append_log(f"Update failed: {message}")
            self._set_update(state="error", error=message)
            return {"ok": False, "error": message}

        self._set_update(state="installed")
        self._append_log(f"Updated to {release.version} — restarting")
        return {"ok": True, "restarting": True, "version": release.version}

    def set_check_updates(self, enabled: bool) -> bool:
        self.settings.check_updates = bool(enabled)
        self._persist()
        if self.settings.check_updates:
            self.check_for_update()
        return self.settings.check_updates

    def open_release_page(self) -> None:
        import webbrowser  # noqa: PLC0415

        webbrowser.open(updates.RELEASES_PAGE)

    def close_window(self) -> None:
        """Close the dashboard from the page, after the update has been started.

        The new build is already running by the time this is called, and two
        copies of the app cannot both hold the port — so this is the second half
        of :meth:`install_update`, not a general-purpose quit button.
        """
        try:
            import webview  # noqa: PLC0415

            for window in list(webview.windows):
                window.destroy()
        except Exception:  # noqa: BLE001 - closing must not raise into the page
            log.exception("could not close the dashboard window")

    def shutdown(self) -> None:
        self.server.stop()


# --- entry points -----------------------------------------------------------

def _device_type(value) -> DeviceType:
    """Accept a name (``"BUZZ"``) or a wire value from the dashboard."""
    if isinstance(value, str):
        try:
            return DeviceType[value.strip().upper()]
        except KeyError:
            pass
    try:
        return DeviceType(int(value))
    except (TypeError, ValueError):
        return DeviceType.XBOX360


def _make_backend(simulate: bool) -> tuple[PadBackend, bool, str | None]:
    """Return ``(backend, simulated, error)``."""
    if simulate:
        return FakeBackend(), True, None
    try:
        return VGamepadBackend(), False, None
    except DriverUnavailableError as exc:
        return FakeBackend(), True, str(exc)


def run_gui(simulate: bool = False) -> int:
    import webview  # noqa: PLC0415

    backend, simulated, driver_error = _make_backend(simulate)
    api = Api(backend, simulated=simulated)

    # Deliberately here and not in Api.__init__. Constructing the API object is
    # what the suite does hundreds of times, and a constructor that reaches for
    # GitHub would make the tests depend on the network — and, on a machine
    # without one, wait for a DNS timeout each time. This is the I/O shell; the
    # check belongs in it.
    updates.clear_backup()   # the build the last update replaced; nothing holds it now
    if api.settings.check_updates:
        api.check_for_update()

    if driver_error:
        api._append_log(f"ViGEmBus driver not available — running in simulation mode: {driver_error}")
    elif simulated:
        api._append_log("Simulation mode: no virtual controller will be created")

    window = webview.create_window(
        f"Nexus Controller {__version__}",
        str(WEB_DIR / "index.html"),
        js_api=api,
        width=1080,
        height=780,
        min_size=(900, 640),
        background_color="#0b0f14",
    )
    try:
        webview.start(debug=False)
    finally:
        api.shutdown()
    return 0


def run_headless(simulate: bool = False) -> int:
    """Run the server with no window — handy for testing and for autostart."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    )
    backend, simulated, driver_error = _make_backend(simulate)
    if driver_error:
        log.warning("ViGEmBus unavailable, simulating: %s", driver_error)

    # The same housekeeping the windowed app does. A machine that only ever runs
    # headless — an autostart entry, a spare PC in the corner — kept every build
    # an update had ever replaced, because the only call to this was over there.
    updates.clear_backup()

    store = SettingsStore()
    settings = store.load()
    desktop = DesktopControl(
        create_backend(), enabled=settings.desktop_control, slot=settings.desktop_slot
    )
    server = ControllerServer(settings, backend, desktop)
    try:
        server.start()
    except OSError as exc:
        log.error("could not start: %s", exc)
        return 1

    log.info("Pairing payload: %s", server.pairing_payload)
    log.info("Press Ctrl+C to stop")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        server.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexus-controller", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--headless", action="store_true", help="run without the dashboard window"
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="do not create real virtual pads (no ViGEmBus needed)",
    )
    args = parser.parse_args(argv)
    return run_headless(args.simulate) if args.headless else run_gui(args.simulate)


if __name__ == "__main__":
    sys.exit(main())
