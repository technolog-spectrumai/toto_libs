"""The names this platform answers to, for the links in a room's messages
(2026-10-02, stage 47.4).

A URL in a message becomes a link only when it points at this platform
(static/forum/linkify.js; the owner: "links to itself" are links, "anywhere
outside it will be just text"). The page knows its own host; this adds the
platform's other public names, so a link pasted from the domain, the tailnet
name or the bare address works on any of them:

* PLATFORM_DOMAIN, MONIT_CERT_DOMAIN (the certificate's name) and the host of
  TAILNET_PUBLIC_URL;
* ALLOWED_HOSTS (deploy.py's ALLOWED_HOSTS_EXTRA is already in it): a
  leading-dot entry gives its bare name only, never "every subdomain" — a
  sibling service on a subdomain is not this platform — and "*" gives
  nothing.

Left out: names without a dot (the compose aliases web-zenobia and nginx,
Django's testserver, localhost), which no reader's browser reaches the
platform by, and loopback addresses, which in a message mean the reader's
own machine. When the page itself is open on one of them, the page's own
host still counts.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from django.conf import settings


def _name(value) -> str:
    """One host name, lower case, ASCII, no port; "" when it is not one."""
    value = str(value or "").strip()
    if not value or value == "*":
        return ""
    try:
        host = urlsplit(value if "://" in value else "//" + value).hostname or ""
    except ValueError:
        return ""
    host = host.strip(".").lower()
    if not host:
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if address.is_loopback or address.is_unspecified:
            return ""
        # The browser's URL.hostname keeps the brackets of an IPv6 address.
        return f"[{address.compressed}]" if address.version == 6 else address.compressed
    if "." not in host or host.endswith(".localhost"):
        return ""
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def platform_hosts() -> list[str]:
    """This platform's public host names, in settings order, each once."""
    values = [
        getattr(settings, "PLATFORM_DOMAIN", ""),
        getattr(settings, "MONIT_CERT_DOMAIN", ""),
        getattr(settings, "TAILNET_PUBLIC_URL", ""),
        *(getattr(settings, "ALLOWED_HOSTS", None) or []),
    ]
    names: list[str] = []
    for value in values:
        name = _name(value)
        if name and name not in names:
            names.append(name)
    return names
