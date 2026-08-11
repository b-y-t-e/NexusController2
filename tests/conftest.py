import socket
import time
from contextlib import closing

import pytest

from nexus_server.config import Settings
from nexus_server.desktop import DesktopControl, FakeDesktop
from nexus_server.devices import FakeBackend
from nexus_server.server import ControllerServer


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    with closing(socket.socket(socket.AF_INET, kind)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, timeout: float = 3.0, interval: float = 0.005):
    """Poll ``predicate`` until it is truthy. Returns its value, or raises."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


@pytest.fixture()
def settings():
    return Settings(
        token="0" * 32,
        bind_ip="127.0.0.1",
        port=free_port(),
        discovery_port=free_port(socket.SOCK_DGRAM),
        manage_firewall=False,
        manage_adb=False,
        discovery_enabled=False,
        server_name="test-pc",
    )


@pytest.fixture()
def backend():
    return FakeBackend()


@pytest.fixture()
def desktop_backend():
    return FakeDesktop()


@pytest.fixture()
def desktop(desktop_backend):
    return DesktopControl(desktop_backend)


@pytest.fixture()
def server(settings, backend, desktop):
    messages: list[str] = []
    instance = ControllerServer(settings, backend, desktop, log_sink=messages.append)
    instance.log_messages = messages
    instance.start()
    try:
        yield instance
    finally:
        instance.stop()
