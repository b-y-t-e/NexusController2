"""Per-player session state and slot allocation."""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field

from .desktop import KeyBindingEngine
from .devices import VirtualPad
from .padconfig import PadConfig
from .protocol import DeviceType, InputState, axis_to_float

log = logging.getLogger(__name__)


@dataclass
class Visuals:
    """Latest stick/trigger positions, for the dashboard."""

    lx: float = 0.0
    ly: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    lt: float = 0.0
    rt: float = 0.0
    buttons_low: int = 0
    buttons_high: int = 0

    @classmethod
    def from_state(cls, state: InputState) -> "Visuals":
        return cls(
            lx=axis_to_float(state.lx),
            ly=axis_to_float(state.ly),
            rx=axis_to_float(state.rx),
            ry=axis_to_float(state.ry),
            lt=state.left_trigger / 255.0,
            rt=state.right_trigger / 255.0,
            buttons_low=state.buttons_low,
            buttons_high=state.buttons_high,
        )


class PlayerSession:
    """One player slot.

    A session is *reserved* the moment a connection is accepted and only released
    when its handler thread finishes. The original code set the "connected" flag
    inside the handler thread instead, so two clients connecting in quick
    succession could both be handed the same slot and fight over one virtual pad.
    """

    def __init__(self, index: int) -> None:
        self.index = index
        self.reserved = False
        self.connected = False
        self.device_type: DeviceType = DeviceType.XBOX360
        self.name = ""
        self.address: tuple[str, int] | None = None
        self.pad: VirtualPad | None = None
        self.connection: socket.socket | None = None
        self.packet_count = 0
        self.dropped_packets = 0
        self.connected_at = 0.0
        self.visuals = Visuals()
        self.keys = KeyBindingEngine()
        #: Last configuration the phone reported, or ``None`` until it does.
        self.config: "PadConfig | None" = None
        #: Set while a pushed config has not yet been echoed back by the phone.
        self.config_pending = False

        # Mouse-mode gyro reference.
        self.gyro_centre: tuple[int, int] | None = None
        #: Mouse buttons this slot is holding down on the PC, by where they came
        #: from. They are kept apart because the wire carries an *absolute*
        #: button field in both messages: a pad frame saying "no triggers" would
        #: otherwise let go of the button a finger is still holding on the
        #: trackpad, and the two arrive independently.
        self.touch_buttons = 0      # from MOUSE messages — the trackpad
        self.trigger_buttons = 0    # from mouse-mode INPUT frames — the triggers

        #: Serialises writes to :attr:`connection`. Rumble arrives on a ViGEm
        #: callback thread while PONG/LED go out from the reader thread; without
        #: this lock two ``sendall`` calls can interleave and desync the stream.
        self._send_lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------------

    def attach(self, conn: socket.socket, address: tuple[str, int]) -> None:
        self.connection = conn
        self.address = address
        self.connected = True
        self.connected_at = time.monotonic()
        self.packet_count = 0
        self.dropped_packets = 0

    def release(self) -> None:
        """Tear the session down. Safe to call more than once."""
        conn, self.connection = self.connection, None
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        pad, self.pad = self.pad, None
        if pad is not None:
            try:
                pad.reset()
            finally:
                pad.close()
        self.connected = False
        self.reserved = False
        self.address = None
        self.name = ""
        self.visuals = Visuals()
        self.gyro_centre = None
        self.touch_buttons = 0
        self.trigger_buttons = 0
        self.config = None
        self.config_pending = False

    @property
    def mouse_buttons_held(self) -> int:
        """Everything this slot is holding down, whichever stream pressed it."""
        return self.touch_buttons | self.trigger_buttons

    # -- I/O ----------------------------------------------------------------

    def send(self, payload: bytes) -> bool:
        """Send a framed message to this client. Returns ``False`` if it failed."""
        conn = self.connection
        if conn is None:
            return False
        with self._send_lock:
            try:
                conn.sendall(payload)
                return True
            except OSError as exc:
                log.debug("slot %d send failed: %s", self.index, exc)
                return False

    # -- telemetry ----------------------------------------------------------

    @property
    def uptime(self) -> float:
        return time.monotonic() - self.connected_at if self.connected else 0.0

    def snapshot(self) -> dict:
        # Read once: the client thread can release the session between lines, and
        # a dashboard row that says "connected" with no pad is worse than a stale
        # one that is at least self-consistent.
        pad = self.pad
        return {
            "slot": self.index,
            "connected": self.connected,
            # Taken by a client that has passed the handshake but whose pad is
            # still being created. Without this the dashboard shows the slot as
            # free for that moment, which is exactly when someone looking at it
            # is trying to work out where their phone went.
            "reserved": self.reserved,
            "xinput_index": pad.user_index if pad is not None else None,
            "name": self.name,
            "address": self.address[0] if self.address else "",
            "device_type": int(self.device_type),
            "device_label": self.device_type.label,
            "packets": self.packet_count,
            "dropped": self.dropped_packets,
            "uptime": round(self.uptime, 1),
            "visuals": {
                "lx": round(self.visuals.lx, 3),
                "ly": round(self.visuals.ly, 3),
                "rx": round(self.visuals.rx, 3),
                "ry": round(self.visuals.ry, 3),
                "lt": round(self.visuals.lt, 3),
                "rt": round(self.visuals.rt, 3),
                "buttons_low": self.visuals.buttons_low,
                "buttons_high": self.visuals.buttons_high,
            },
            "bindings": self.keys.bindings,
            "config": self.config.to_dict() if self.config else None,
            "config_pending": self.config_pending,
        }


class SlotAllocator:
    """Hands out player slots atomically."""

    def __init__(self, count: int) -> None:
        self._lock = threading.Lock()
        self.sessions = [PlayerSession(i) for i in range(count)]

    def __len__(self) -> int:
        return len(self.sessions)

    def acquire(self) -> PlayerSession | None:
        """Reserve and return the lowest free slot, or ``None`` when full."""
        with self._lock:
            for session in self.sessions:
                if not session.reserved:
                    session.reserved = True
                    return session
        return None

    def has_free(self) -> bool:
        """Whether a slot is free *right now*.

        Only a hint: the caller cannot rely on the slot still being there, which
        is why :meth:`acquire` is the one that actually decides. It exists so the
        accept loop can turn a hopeless connection away before it costs a thread.
        """
        with self._lock:
            return any(not session.reserved for session in self.sessions)

    def release(self, session: PlayerSession) -> None:
        with self._lock:
            session.release()

    def release_all(self) -> None:
        with self._lock:
            for session in self.sessions:
                session.release()

    @property
    def connected_count(self) -> int:
        return sum(1 for s in self.sessions if s.connected)


class RateLimiter:
    """Token-bucket limiter, used both for input floods and failed handshakes."""

    def __init__(self, capacity: float, per_second: float, *, now: float | None = None) -> None:
        if capacity <= 0 or per_second <= 0:
            raise ValueError("capacity and per_second must be positive")
        self.capacity = float(capacity)
        self.per_second = float(per_second)
        self._tokens = float(capacity)
        self._last = now if now is not None else time.monotonic()

    def allow(self, *, now: float | None = None, cost: float = 1.0) -> bool:
        current = now if now is not None else time.monotonic()
        elapsed = max(0.0, current - self._last)
        self._last = current
        self._tokens = min(self.capacity, self._tokens + elapsed * self.per_second)
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False


@dataclass
class AttemptTracker:
    """Counts failed handshakes per source address inside a sliding window.

    Locked, because the accept thread asks and every client thread answers: the
    pruning does ``bucket[:] = [...]`` while another thread may be appending, and
    a lost record is a failed attempt that never counted.
    """

    max_attempts: int = 5
    window: float = 60.0
    _attempts: dict[str, list[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_failure(self, host: str, *, now: float | None = None) -> None:
        current = now if now is not None else time.monotonic()
        with self._lock:
            bucket = self._attempts.setdefault(host, [])
            bucket.append(current)
            self._prune(host, current)

    def is_blocked(self, host: str, *, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        with self._lock:
            self._prune(host, current)
            return len(self._attempts.get(host, ())) >= self.max_attempts

    def clear(self, host: str) -> None:
        with self._lock:
            self._attempts.pop(host, None)

    def _prune(self, host: str, now: float) -> None:
        bucket = self._attempts.get(host)
        if bucket is None:
            return
        cutoff = now - self.window
        bucket[:] = [t for t in bucket if t >= cutoff]
        if not bucket:
            self._attempts.pop(host, None)
