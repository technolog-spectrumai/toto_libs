"""Thin wrapper over the Tor control protocol (via ``stem``).

faros's tor container exposes a password-authenticated ``ControlPort`` on the
internal docker network (configured by deploy.py when ``tor.control: true``). We
publish the onion as an *ephemeral, detached* v3 hidden service — Tor keeps it up
after the control connection closes, but it does not write a ``HiddenServiceDir``,
so nomad's keystore is the only persistent copy of the key.

Config (settings / env, all ``NOMAD_*``):
    NOMAD_TOR_CONTROL_HOST   control-port host        (default "tor")
    NOMAD_TOR_CONTROL_PORT   control-port tcp port    (default 9051)
    NOMAD_TOR_CONTROL_PASSWORD  cleartext password matching torrc HashedControlPassword
    NOMAD_ONION_PORT         the onion's virtual port (default 443)
    NOMAD_ONION_TARGET       where tor forwards it    (default "nginx:443")

``stem`` is imported lazily so this module (and the tests that mock it) load even
where stem isn't installed.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager

from django.conf import settings

logger = logging.getLogger(__name__)


def _conf(name: str, default):
    return getattr(settings, name, default)


def _ports() -> dict[int, str]:
    return {int(_conf("NOMAD_ONION_PORT", 443)): _conf("NOMAD_ONION_TARGET", "nginx:443")}


@contextmanager
def _controller(retries: int = 30, delay: float = 2.0):
    """Yield an authenticated stem Controller, retrying while tor bootstraps."""
    import socket  # noqa: PLC0415
    import stem  # noqa: PLC0415
    from stem.control import Controller  # noqa: PLC0415

    host = _conf("NOMAD_TOR_CONTROL_HOST", "tor")
    port = int(_conf("NOMAD_TOR_CONTROL_PORT", 9051))
    password = _conf("NOMAD_TOR_CONTROL_PASSWORD", "") or None

    controller = None
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            # stem's from_port validates that `address` is an IP *literal*
            # (otherwise: "Invalid IP address: tor"), so resolve the docker
            # service name to its container IP first. Done inside the retry loop
            # so a not-yet-registered DNS name is retried too.
            ip = socket.gethostbyname(host)
            controller = Controller.from_port(address=ip, port=port)
            break
        except (stem.SocketError, socket.gaierror, OSError) as exc:
            last_exc = exc
            logger.info("nomad: tor control port %s:%s not ready (%s/%s): %s", host, port, attempt, retries, exc)
            time.sleep(delay)
    if controller is None:
        raise RuntimeError(f"nomad: could not reach tor control port {host}:{port}: {last_exc}")

    try:
        controller.authenticate(password=password)
        yield controller
    finally:
        controller.close()


def mint() -> tuple[str, str]:
    """Generate + publish a brand-new onion. Returns (service_id, private_key)."""
    with _controller() as c:
        resp = c.create_ephemeral_hidden_service(
            _ports(),
            key_type="NEW",
            key_content="ED25519-V3",
            detached=True,
            await_publication=True,
        )
        return resp.service_id, f"{resp.private_key_type}:{resp.private_key}"


def publish(private_key: str) -> str:
    """(Re)publish an existing onion from a stored "TYPE:base64" key. Returns service_id."""
    key_type, _, key_content = private_key.partition(":")
    with _controller() as c:
        resp = c.create_ephemeral_hidden_service(
            _ports(),
            key_type=key_type,
            key_content=key_content,
            detached=True,
            await_publication=True,
        )
        return resp.service_id


def unpublish(service_id: str) -> None:
    """Tear down a published onion. Best-effort — logs, never raises."""
    if not service_id:
        return
    try:
        with _controller() as c:
            c.remove_ephemeral_hidden_service(service_id)
    except Exception as exc:  # noqa: BLE001 — tearing down the old onion must not block a migration
        logger.warning("nomad: failed to unpublish %s.onion: %s", service_id, exc)
