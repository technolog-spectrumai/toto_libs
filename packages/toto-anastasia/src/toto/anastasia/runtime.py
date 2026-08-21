"""The seam between booking and the thing that actually mounts a Gear.

The assets-backend shape, which ``lifecycle/power.py`` also copies: a stateless
ABC, a safe default that records and refuses, and a factory that resolves a
dotted path from settings LAZILY so ``override_settings`` can move it in tests.

Stage 1 ships only the null backend, so every service in this package is
testable with no Docker, no manager and no network. The manager-backed
implementation arrives in ``toto.anastasia.manager_backend`` and is selected by
setting ``ANASTASIA_RUNTIME_BACKEND``.

Backends must be stateless — do not store per-request state on ``self``.
"""

from __future__ import annotations

import logging

from django.utils.module_loading import import_string

from . import conf

log = logging.getLogger("toto.anastasia.runtime")


class RuntimeUnavailable(Exception):
    """The backend cannot act, and says why in a sentence a user can act on."""


class RuntimeBackend:
    """What booking needs from whatever owns the containers."""

    name = "abstract"

    def mount(self, lease) -> dict:
        """Create the Gear's bounded environment. Returns a status mapping."""
        raise NotImplementedError

    def unmount(self, lease) -> dict:
        """Destroy every runner and the environment. MUST be idempotent —
        unmounting an already-unmounted Gear is an ordinary outcome, not an
        error, because that is exactly what reconciliation does."""
        raise NotImplementedError

    def status(self, lease) -> dict:
        """Live usage and health, or ``{}`` when nothing is known."""
        raise NotImplementedError

    def start_execution(self, execution, *, params, payload) -> dict:
        raise NotImplementedError

    def kill_execution(self, execution) -> dict:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"backend": f"{type(self).__module__}.{type(self).__name__}",
                "name": self.name}


class NullRuntimeBackend(RuntimeBackend):
    """The safe default: booking works, mounting does not.

    Not a stub that pretends. A host with no manager configured can still let
    people reserve and release capacity — that is pure arithmetic in this
    database — but it must not report a Gear as mounted when nothing was
    mounted. So mount refuses, loudly, naming what is missing; unmount succeeds
    (there is provably nothing to tear down, and an idempotent teardown that
    refuses would block release); and status reports nothing known.
    """

    name = "null"

    def mount(self, lease):
        raise RuntimeUnavailable(
            "This deployment has no Anastasia manager, so a Gear cannot be "
            "mounted. Set ANASTASIA_MANAGER_URL and ANASTASIA_RUNTIME_BACKEND, "
            "and start the anastasia_manager service.")

    def unmount(self, lease):
        return {"unmounted": True, "runners_destroyed": 0}

    def status(self, lease):
        return {}

    def start_execution(self, execution, *, params, payload):
        raise RuntimeUnavailable(
            "This deployment has no Anastasia manager, so nothing can run.")

    def kill_execution(self, execution):
        return {"killed": False}


def get_backend() -> RuntimeBackend:
    """Resolve the configured backend, falling back to the safe one.

    A misconfigured dotted path must not take the whole host down: booking is
    still arithmetic that works, and the refusal a NullRuntimeBackend produces
    names the problem far better than an ImportError at boot would.
    """
    path = conf.runtime_backend_path()
    try:
        return import_string(path)()
    except ImportError:
        log.exception("anastasia: runtime backend %s could not be imported", path)
        return NullRuntimeBackend()
