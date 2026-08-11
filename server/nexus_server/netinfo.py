"""Local network address discovery."""

from __future__ import annotations

import ipaddress
import logging
import socket

log = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"


def primary_ip() -> str:
    """Best guess at the LAN address other devices should connect to.

    Opens an unconnected UDP socket towards a public address purely to ask the
    routing table which local interface would be used; no packet is sent.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return LOOPBACK
    finally:
        sock.close()


def local_ips() -> list[str]:
    """Every usable IPv4 address on this host, best candidate first."""
    found: list[str] = []
    primary = primary_ip()
    if primary != LOOPBACK:
        found.append(primary)

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in found and _is_usable(address):
                found.append(address)
    except OSError as exc:
        log.debug("getaddrinfo failed: %s", exc)

    found.append(LOOPBACK)
    return found


def _is_usable(address: str) -> bool:
    try:
        parsed = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        return False
    return not (parsed.is_loopback or parsed.is_link_local or parsed.is_multicast)


def is_valid_ipv4(address: str) -> bool:
    try:
        ipaddress.IPv4Address(address)
        return True
    except ipaddress.AddressValueError:
        return False
