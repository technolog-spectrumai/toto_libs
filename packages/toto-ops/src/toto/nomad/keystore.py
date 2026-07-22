"""Persistent store for the onion's ed25519 private key — nomad's source of truth.

The key is written to ``current.json`` in ``settings.NOMAD_KEY_DIR`` (the
``nomad_data`` Docker volume). Keeping it here — rather than in the DB — means the
onion address survives ``RESET=1`` DB wipes and tor/web restarts; only the
"Migrate" action changes it.

Plaintext on disk is acceptable: tor itself stored the same secret unencrypted in
``tor_data/<svc>/hs_ed25519_secret_key``, so the threat model already trusts the
host volumes.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


def _key_dir() -> Path:
    return Path(getattr(settings, "NOMAD_KEY_DIR", "/var/lib/nomad"))


def _key_file() -> Path:
    return _key_dir() / "current.json"


def load_key() -> tuple[str, str] | None:
    """Return (service_id, private_key), or None if no key is stored yet."""
    path = _key_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data["service_id"], data["private_key"]
    except (ValueError, OSError, KeyError) as exc:
        logger.error("nomad: could not read key file %s: %s", path, exc)
        return None


def save_key(service_id: str, private_key: str) -> None:
    """Atomically persist the key with 0600 perms."""
    directory = _key_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(directory))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump({"service_id": service_id, "private_key": private_key}, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _key_file())
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
