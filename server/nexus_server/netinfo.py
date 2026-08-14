"""Local network address discovery."""

from __future__ import annotations

import ipaddress
import logging
import socket

log = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"
#: Bind here to accept connections on every interface at once.
#:
#: The address to *listen* on, never one to connect to: a phone told to dial
#: 0.0.0.0 gets nowhere. Anything that names the server to somebody else — the
#: QR code, the pairing line, the firewall's idea of which network this is —
#: uses :func:`primary_ip` instead. See :func:`advertised_ip`.
ALL_INTERFACES = "0.0.0.0"


def advertised_ip(bind_ip: str) -> str:
    """The address to hand a phone, for a server bound to ``bind_ip``.

    The same address for every bind but the wildcard one, which has no address
    of its own — there the best guess at the LAN address is what the QR code has
    always carried anyway.
    """
    if not bind_ip or bind_ip == ALL_INTERFACES:
        return primary_ip()
    return bind_ip


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
