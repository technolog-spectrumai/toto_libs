"""The visitor's address, said in one place (2026-09-30).

Behind a deployed stack every request reaches Django from nginx, so
``REMOTE_ADDR`` is nginx's address on the compose network and the visitor's
is in ``X-Real-IP``, which nginx sets on every location it proxies
(``proxy_set_header X-Real-IP $remote_addr``, scripts/deploy.py). A header is
only as honest as whoever set it, so it is believed only when the request
came from a trusted proxy:

* ``TRUSTED_PROXIES`` — addresses or networks (a list, or one comma-separated
  string). Loopback only when unset; a host whose web container is reachable
  only through its own nginx names the compose network's private ranges.
* ``TRUST_X_REAL_IP = True`` — every peer is a proxy. Only for a deployment
  where nothing but the proxy can reach Django at all.

Otherwise, and whenever the header is missing or is not an address, the
answer is ``REMOTE_ADDR``. ``X-Forwarded-For`` is not read: its first entry
is whatever the client wrote there, because nginx appends to it rather than
replacing it.

    client_ip(request)  -> "203.0.113.7" | "2001:db8::1" | ""

An IPv4 address carried as IPv6 (``::ffff:203.0.113.7``) is answered as the
IPv4 one, so one visitor is never two. The sign-in lockout
(``toto.core.signin_lockout``) keys on this; the audit chain's request source
is to follow.
"""

from __future__ import annotations

import ipaddress
import logging
from functools import lru_cache

from django.conf import settings

log = logging.getLogger(__name__)

#: Used when a host sets no TRUSTED_PROXIES: a proxy on the same machine.
DEFAULT_TRUSTED_PROXIES = ("127.0.0.0/8", "::1/128")


def parse_address(value):
    """An ``ipaddress`` address from ``value``, or None when it is not one."""
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    text = text.split("%", 1)[0]  # an IPv6 zone id names an interface, not a host
    if not text:
        return None
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    return getattr(address, "ipv4_mapped", None) or address


@lru_cache(maxsize=16)
def _networks(entries: tuple) -> tuple:
    networks = []
    for entry in entries:
        entry = str(entry).strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            # A typo trusts nothing rather than everything, and does not stop
            # the site: the header is then simply not believed from there.
            log.warning("TRUSTED_PROXIES: %r is not an address or a network; ignored", entry)
    return tuple(networks)


def trusted_networks() -> tuple:
    configured = getattr(settings, "TRUSTED_PROXIES", None)
    if configured is None:
        configured = DEFAULT_TRUSTED_PROXIES
    if isinstance(configured, str):
        configured = configured.split(",")
    return _networks(tuple(str(entry) for entry in configured))


def is_trusted_proxy(address) -> bool:
    address = parse_address(address)
    if address is None:
        return False
    return any(address.version == network.version and address in network
               for network in trusted_networks())


def client_ip(request) -> str:
    """The visitor's address as text, or "" when there is none to be had."""
    meta = getattr(request, "META", None) or {}
    peer = parse_address(meta.get("REMOTE_ADDR"))
    forwarded = parse_address(meta.get("HTTP_X_REAL_IP"))
    if forwarded is not None and (
            getattr(settings, "TRUST_X_REAL_IP", False)
            or (peer is not None and is_trusted_proxy(peer))):
        return str(forwarded)
    return str(peer) if peer is not None else ""
