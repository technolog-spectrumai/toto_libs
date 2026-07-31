"""Fetch the parent platform's public identity — the child side of platform-info.

The mirror of ``sso_master.views.platform_info``: this calls the parent's
``GET /sso/platform-info/`` with THIS host's own OIDC ``client_id`` + ``client_secret``
(HTTP Basic), which both reads the parent's name/domain/logo and proves the pairing
secret still works. So it doubles as the federation console's "Test" action.

A soft edge like ``core.email_config``: it never raises. A locked vault (empty secret),
an unreachable parent, a rejected credential, or malformed JSON all return ``None``, and
the console falls back to the locally stored config.
"""
from __future__ import annotations

import base64
import logging

logger = logging.getLogger(__name__)


def platform_info_url(portal_url: str) -> str:
    return f"{portal_url.rstrip('/')}/sso/platform-info/"


def fetch_platform_info(config: dict | None = None) -> dict | None:
    """The parent's ``{site_name, domain, logo_url, federation, …}``, or ``None``.

    ``config`` is the ``sso_client`` config dict (``apps.get_config()``); read it here
    when not supplied. Uses the same back-channel plumbing as the OIDC token calls —
    ``views.http_requests`` (patchable in tests), ``_tls_verify`` and the short timeout.
    """
    # Lazy import: views imports plenty at module load, and importing it here at module
    # scope would make views <-> parent_info circular.
    from django.apps import apps

    from . import views

    if config is None:
        config = apps.get_app_config("sso_client").get_config()
    portal_url = (config.get("portal_url") or "").strip()
    client_id = (config.get("client_id") or "").strip()
    client_secret = config.get("client_secret") or ""
    if not (portal_url and client_id and client_secret):
        return None

    raw = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        resp = views.http_requests.get(
            platform_info_url(portal_url),
            headers={"Authorization": f"Basic {raw}"},
            timeout=views._BACKCHANNEL_TIMEOUT,
            verify=views._tls_verify(),
        )
    except Exception as exc:                    # noqa: BLE001 — unreachable parent, TLS, DNS…
        logger.warning("platform-info fetch failed: %s", exc)
        return None
    if resp.status_code != 200:
        logger.warning("platform-info returned HTTP %s", resp.status_code)
        return None
    try:
        return resp.json()
    except Exception:                           # noqa: BLE001 — non-JSON body
        return None
