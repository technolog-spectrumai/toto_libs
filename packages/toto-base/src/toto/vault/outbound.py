"""Where this host is allowed to make an outbound request to.

The vault fetches two operator-supplied URLs server-side: ``BucketPeer.base_url``
(via :mod:`toto.vault.peer_client`) and the S3 ``endpoint_url`` (handed to
botocore). Until this module existed, **neither was validated anywhere in the
suite** — an operator could point either at ``169.254.169.254`` and read the
cloud metadata service, or at an internal address the host can reach and the
operator cannot.

## The policy, in order

1. An explicit ``VAULT_OUTBOUND_ALLOWED_HOSTS`` entry **allows** the host and
   skips every other check. An operator who names their internal MinIO has made
   the decision deliberately, and that is the legitimate private-address case.
2. Otherwise, every address the host resolves to must be public. A literal IP
   is checked directly.
3. **An unresolvable host is allowed through.** Two reasons, both load-bearing:
   a name that does not resolve cannot be connected to either, so refusing buys
   nothing; and refusing on DNS failure would turn every offline test suite red,
   since the fixture peer host resolves nowhere.
4. Plain ``http://`` is refused unless the host came through (1) or
   ``VAULT_OUTBOUND_ALLOW_PRIVATE`` is on.

Note this is deliberately NOT the ``API_CONNECTOR_ALLOWED_HOSTS`` shape
(``toto/api/client.py``), which fails **open** when unset. Here "unset" means
steps 2-4 still apply, which is fail-closed on the ranges that matter.

## Known residual: DNS rebinding

The guard resolves at check time; a hostile resolver can answer differently at
connect time. Closing that needs a transport adapter pinning the checked IP for
the connection — a rewrite of ``peer_client._http()`` plus a botocore endpoint
resolver. It is not covered here, and saying so is better than implying it is.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit

from django.conf import settings

#: Hostname shape, per label. Rejects the empty label and anything with a
#: character an authority component has no business containing.
_HOSTNAME = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$")


class OutboundRefused(ValueError):
    """This host may not be contacted, and the message says which and why."""


def _allowed_hosts() -> set[str]:
    return {h.strip().lower() for h in getattr(settings, "VAULT_OUTBOUND_ALLOWED_HOSTS", []) if h.strip()}


def _allow_private() -> bool:
    return bool(getattr(settings, "VAULT_OUTBOUND_ALLOW_PRIVATE", False))


def canonical_outbound_url(raw: str, *, label: str = "URL") -> str:
    """Canonicalise an operator-typed URL, or refuse it by name.

    Adapted from ``toto.sso_client.pairing.platform_url`` — including the check
    that matters most and is easy to leave out:

        Refuse anything an HTTP client would resolve to a different host than
        ``urlsplit`` reports. ``urlsplit`` reads ``https://evil.com\\@good.com``
        as host ``good.com`` (treating ``evil.com\\`` as userinfo), but
        requests/urllib3 terminate the authority at the backslash and connect
        to ``evil.com``.
    """
    text = (raw or "").strip()
    if not text:
        raise OutboundRefused(f"{label} is empty.")
    if "\\" in text:
        raise OutboundRefused(
            f"{label} contains a backslash, which HTTP clients and URL parsers "
            "disagree about. Remove it.")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise OutboundRefused(f"{label} must be an http(s) URL.")
    if parts.username is not None or parts.password is not None:
        raise OutboundRefused(
            f"{label} carries credentials in the URL. Put the host alone here.")
    host = (parts.hostname or "").lower()
    if not host:
        raise OutboundRefused(f"{label} names no host.")
    if not _is_ip(host) and not _HOSTNAME.fullmatch(host):
        raise OutboundRefused(f"{label} is not a valid hostname: {host!r}")
    # An IPv6 literal must keep its brackets or the rebuilt URL is unparseable:
    # "https://::1" re-splits to a host that is neither an address nor a name,
    # and the guard below would then wave it through as "unresolvable".
    netloc = f"[{host}]" if _is_ipv6(host) else host
    if parts.port and not (
        (parts.scheme == "https" and parts.port == 443)
        or (parts.scheme == "http" and parts.port == 80)
    ):
        netloc = f"{netloc}:{parts.port}"
    return f"{parts.scheme}://{netloc}{parts.path.rstrip('/')}"


def _is_ipv6(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.strip("[]")).version == 6
    except ValueError:
        return False


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def _is_public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return not (
        ip.is_loopback or ip.is_private or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def assert_outbound_allowed(url: str, *, label: str = "URL") -> str:
    """Refuse an outbound target that points inward. Returns the canonical URL."""
    canonical = canonical_outbound_url(url, label=label)
    parts = urlsplit(canonical)
    host = (parts.hostname or "").lower()

    if host in _allowed_hosts():
        return canonical

    if parts.scheme == "http" and not _allow_private():
        raise OutboundRefused(
            f"{label} uses plain http. Use https, or name the host in "
            "VAULT_OUTBOUND_ALLOWED_HOSTS if it is an internal service.")

    if _is_ip(host):
        addresses = [host.strip("[]")]
    else:
        try:
            addresses = [info[4][0] for info in socket.getaddrinfo(host, None)]
        except socket.gaierror:
            # Step 3: unresolvable is unreachable. Refusing buys nothing and
            # would redden every offline test suite.
            return canonical

    for addr in addresses:
        if not _is_public(addr):
            raise OutboundRefused(
                f"{label} resolves to {addr}, which is not a public address. "
                "If that is deliberate, name the host in "
                "VAULT_OUTBOUND_ALLOWED_HOSTS.")
    return canonical
