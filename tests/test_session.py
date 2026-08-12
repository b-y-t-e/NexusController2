"""Slot allocation, rate limiting and per-session bookkeeping."""

import socket
import threading

import pytest

from nexus_server.protocol import DeviceType, InputState
from nexus_server.session import (
    AttemptTracker,
    PlayerSession,
    RateLimiter,
    SlotAllocator,
    Visuals,
)


class TestSlotAllocator:
    def test_hands_out_lowest_free_slot(self):
        allocator = SlotAllocator(4)
        assert [allocator.acquire().index for _ in range(4)] == [0, 1, 2, 3]

    def test_returns_none_when_full(self):
        allocator = SlotAllocator(2)
        allocator.acquire()
        allocator.acquire()
        assert allocator.acquire() is None

    def test_has_free_tracks_reservations(self):
        """The accept loop's early "don't even bother" check.

        It is only a hint — :meth:`acquire` is what actually decides — but a
        wrong answer either turns good clients away or lets the queue fill up
        with connections that can never be served.
        """
        allocator = SlotAllocator(2)
        assert allocator.has_free() is True
        first = allocator.acquire()
        assert allocator.has_free() is True
        allocator.acquire()
        assert allocator.has_free() is False
        allocator.release(first)
        assert allocator.has_free() is True

    def test_released_slot_is_reused(self):
        allocator = SlotAllocator(2)
        first = allocator.acquire()
        allocator.acquire()
        allocator.release(first)
        assert allocator.acquire() is first

    def test_no_slot_is_handed_out_twice_under_contention(self):
        """The original server checked a flag that the *client thread* set later,
        so two fast connections could share one slot and one virtual pad."""
        allocator = SlotAllocator(64)
        acquired: list[PlayerSession] = []
        lock = threading.Lock()
        barrier = threading.Barrier(16)

        def worker():
            barrier.wait()
            for _ in range(4):
                session = allocator.acquire()
                if session is not None:
                    with lock:
                        acquired.append(session)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(acquired) == 64
        assert len({id(s) for s in acquired}) == 64

    def test_connected_count(self):
        allocator = SlotAllocator(3)
        session = allocator.acquire()
        assert allocator.connected_count == 0  # reserved is not yet connected
        session.connected = True
        assert allocator.connected_count == 1

    def test_release_all(self):
        allocator = SlotAllocator(3)
        for _ in range(3):
            allocator.acquire()
        allocator.release_all()
        assert allocator.acquire().index == 0


class TestPlayerSession:
    def test_release_closes_the_socket(self):
        """v1 never closed client sockets, leaking a handle per reconnect."""
        left, right = socket.socketpair()
        session = PlayerSession(0)
        session.attach(left, ("127.0.0.1", 1234))
        session.release()
        assert session.connection is None
        with pytest.raises(OSError):
            left.send(b"x")
        right.close()

    def test_release_is_idempotent(self):
        session = PlayerSession(0)
        session.release()
        session.release()

    def test_release_resets_and_closes_the_pad(self):
        from nexus_server.devices import FakePad

        session = PlayerSession(0)
        session.pad = FakePad(DeviceType.XBOX360)
        pad = session.pad
        session.release()
        assert pad.reset_count == 1 and pad.closed

    def test_send_returns_false_without_a_connection(self):
        assert PlayerSession(0).send(b"x") is False

    def test_send_reports_failure_on_a_dead_socket(self):
        left, right = socket.socketpair()
        session = PlayerSession(0)
        session.attach(left, ("127.0.0.1", 1))
        right.close()
        left.close()
        assert session.send(b"hello") is False

    def test_concurrent_sends_do_not_interleave(self):
        """Rumble arrives on a ViGEm callback thread while PONG goes out from the
        reader thread; without the send lock the two can be spliced together."""
        left, right = socket.socketpair()
        session = PlayerSession(0)
        session.attach(left, ("127.0.0.1", 1))
        message_a = b"A" * 64
        message_b = b"B" * 64
        start = threading.Barrier(2)

        def sender(payload):
            start.wait()
            for _ in range(50):
                session.send(payload)

        threads = [threading.Thread(target=sender, args=(m,)) for m in (message_a, message_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        session.release()

        received = bytearray()
        while chunk := right.recv(65536):
            received += chunk
        right.close()

        assert len(received) == 100 * 64
        for offset in range(0, len(received), 64):
            block = received[offset:offset + 64]
            assert block in (message_a, message_b), f"interleaved at offset {offset}"

    def test_snapshot_shape(self):
        session = PlayerSession(2)
        session.device_type = DeviceType.BUZZ
        snap = session.snapshot()
        assert snap["slot"] == 2
        assert snap["device_label"] == "Buzz (PS3)"
        assert set(snap["visuals"]) >= {"lx", "ly", "rx", "ry", "lt", "rt"}

    def test_snapshot_distinguishes_reserved_from_connected(self):
        """The dashboard has to be able to say "connecting", not "free".

        Between a valid HELLO and the pad being created the slot is spoken for
        but not yet live. Reporting only ``connected`` made that moment look like
        an empty slot — the one reading that sends someone hunting for a phone
        that is in fact arriving.
        """
        session = PlayerSession(0)
        session.reserved = True
        snap = session.snapshot()
        assert snap["reserved"] is True
        assert snap["connected"] is False

    def test_snapshot_reports_no_xinput_number_without_a_pad(self):
        assert PlayerSession(0).snapshot()["xinput_index"] is None


class TestVisuals:
    def test_from_state(self):
        visuals = Visuals.from_state(
            InputState(lx=127, ly=-127, left_trigger=255, right_trigger=0, buttons_low=0x03)
        )
        assert visuals.lx == pytest.approx(1.0)
        assert visuals.ly == pytest.approx(-1.0)
        assert visuals.lt == pytest.approx(1.0)
        assert visuals.rt == 0.0
        assert visuals.buttons_low == 0x03


class TestRateLimiter:
    def test_allows_up_to_capacity(self):
        limiter = RateLimiter(5, 5, now=0.0)
        assert all(limiter.allow(now=0.0) for _ in range(5))
        assert limiter.allow(now=0.0) is False

    def test_refills_over_time(self):
        limiter = RateLimiter(10, 10, now=0.0)
        for _ in range(10):
            limiter.allow(now=0.0)
        assert limiter.allow(now=0.0) is False
        assert limiter.allow(now=1.0) is True

    def test_never_exceeds_capacity(self):
        limiter = RateLimiter(3, 100, now=0.0)
        limiter.allow(now=1000.0)
        assert sum(limiter.allow(now=1000.0) for _ in range(10)) == 2

    def test_rejects_invalid_configuration(self):
        with pytest.raises(ValueError):
            RateLimiter(0, 1)
        with pytest.raises(ValueError):
            RateLimiter(1, 0)


class TestAttemptTracker:
    def test_blocks_after_the_limit(self):
        tracker = AttemptTracker(max_attempts=3, window=60)
        for _ in range(2):
            tracker.record_failure("10.0.0.1", now=0.0)
        assert tracker.is_blocked("10.0.0.1", now=0.0) is False
        tracker.record_failure("10.0.0.1", now=0.0)
        assert tracker.is_blocked("10.0.0.1", now=0.0) is True

    def test_window_expires(self):
        tracker = AttemptTracker(max_attempts=1, window=60)
        tracker.record_failure("10.0.0.1", now=0.0)
        assert tracker.is_blocked("10.0.0.1", now=30.0) is True
        assert tracker.is_blocked("10.0.0.1", now=61.0) is False

    def test_hosts_are_independent(self):
        tracker = AttemptTracker(max_attempts=1, window=60)
        tracker.record_failure("10.0.0.1", now=0.0)
        assert tracker.is_blocked("10.0.0.2", now=0.0) is False

    def test_success_clears(self):
        tracker = AttemptTracker(max_attempts=1, window=60)
        tracker.record_failure("10.0.0.1", now=0.0)
        tracker.clear("10.0.0.1")
        assert tracker.is_blocked("10.0.0.1", now=0.0) is False
