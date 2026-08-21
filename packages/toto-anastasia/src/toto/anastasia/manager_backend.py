"""The Django side of the wire: a RuntimeBackend that calls the manager.

This is the ONLY module in the app that knows the manager exists. Everything
else — services, execute, the views — talks to ``runtime.RuntimeBackend``, so a
host with no manager keeps working (booking is arithmetic) and the tests keep
running with a fake.

Uses ``urllib`` rather than ``requests``: the suite pins requests, but this call
crosses an internal network to a service we sign for ourselves, and urllib is
one less dependency in the path that must never fail obscurely.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request

from . import conf
from .limits import Limits
from .manager import protocol
from .runtime import RuntimeBackend, RuntimeUnavailable

log = logging.getLogger("toto.anastasia.manager_backend")

#: Long enough for a manager that is unpacking a staged input, short enough
#: that a wedged manager does not hold a celery worker for the whole job.
#: Starting an execution RETURNS as soon as the container is running — it does
#: not wait for the job — so this bounds setup, not work.
DEFAULT_TIMEOUT = 60


class ManagerRuntimeBackend(RuntimeBackend):
    """Stateless, per the ABC's contract — a fresh one per ``get_backend()``."""

    name = "manager"

    def _call(self, method: str, path: str, payload: dict | None = None,
              *, timeout: int = DEFAULT_TIMEOUT) -> dict:
        base = conf.manager_url()
        if not base:
            raise RuntimeUnavailable(
                "This deployment has no Anastasia manager address configured "
                "(ANASTASIA_MANAGER_URL), so Gears cannot be mounted.")
        secret = conf.shared_secret()
        if not secret:
            raise RuntimeUnavailable(
                "The Anastasia manager is configured without a shared secret, "
                "so this host cannot sign requests to it.")

        body = protocol.encode(payload or {})
        headers = protocol.sign(secret=secret, method=method, path=path,
                                body=body)
        headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = _error_sentence(exc)
            if exc.code == 401:
                # Never surface "unauthenticated" to a user: it is an operator
                # problem (a secret mismatch), and nothing they do will fix it.
                log.error("anastasia: the manager rejected our signature — "
                          "ANASTASIA_SHARED_SECRET differs between this host "
                          "and the manager")
                raise RuntimeUnavailable(
                    "This host and the compute manager are not configured with "
                    "the same secret, so it refused the request. An "
                    "administrator needs to look at it.") from exc
            raise RuntimeUnavailable(detail) from exc
        except urllib.error.URLError as exc:
            raise RuntimeUnavailable(
                f"The compute manager is not answering ({exc.reason}). Your "
                "reservation is untouched.") from exc
        except (TimeoutError, OSError) as exc:
            raise RuntimeUnavailable(
                f"The compute manager did not answer in time ({exc}).") from exc
        except ValueError as exc:
            raise RuntimeUnavailable(
                f"The compute manager sent something unreadable ({exc}).") from exc

    # -- the RuntimeBackend contract --------------------------------------

    def mount(self, lease) -> dict:
        return self._call("POST", f"/gears/{lease.uuid}/mount",
                          {"limits": lease.limits.as_dict()})

    def unmount(self, lease) -> dict:
        try:
            return self._call("POST", f"/gears/{lease.uuid}/unmount")
        except RuntimeUnavailable:
            # Teardown must never be blockable by an absent manager: with the
            # manager gone, so are its containers, and refusing here would
            # strand the lease. Reconciliation cleans up if it comes back.
            log.warning("anastasia: unmounting %s with no manager answering",
                        lease.uuid)
            return {"unmounted": True, "runners_destroyed": 0,
                    "manager_absent": True}

    def status(self, lease) -> dict:
        try:
            return self._call("GET", f"/gears/{lease.uuid}/status", timeout=15)
        except RuntimeUnavailable:
            return {}

    def start_execution(self, execution, *, params, payload) -> dict:
        body = {
            "gear": str(execution.lease.uuid),
            "execution": str(execution.uuid),
            "operation": execution.operation,
            "params": params,
            "timeout": execution.timeout_seconds,
            "limits": execution.limits.as_dict(),
        }
        if payload:
            body["payload_b64"] = base64.b64encode(payload).decode("ascii")
        return self._call("POST", "/executions", body)

    def kill_execution(self, execution) -> dict:
        return self._call("POST", f"/executions/{execution.uuid}/kill",
                          {"gear": str(execution.lease.uuid)})

    # -- beyond the ABC: what the caller needs to finish a job -------------

    def execution_status(self, execution) -> dict:
        return self._call("GET", f"/executions/{execution.uuid}",
                          {"gear": str(execution.lease.uuid)}, timeout=15)

    def collect(self, execution) -> bytes:
        result = self._call("GET", f"/executions/{execution.uuid}/out",
                            {"gear": str(execution.lease.uuid)}, timeout=120)
        return base64.b64decode(result.get("tar_b64") or "")

    def finish_execution(self, execution) -> dict:
        return self._call("POST", f"/executions/{execution.uuid}/finish",
                          {"gear": str(execution.lease.uuid)})

    def pool(self) -> dict:
        return self._call("GET", "/pool", timeout=15)

    def reconcile(self, known_gears) -> dict:
        return self._call("POST", "/reconcile",
                          {"known_gears": [str(g) for g in known_gears]},
                          timeout=120)


def _error_sentence(exc) -> str:
    try:
        detail = json.loads(exc.read().decode("utf-8") or "{}").get("error")
    except Exception:  # noqa: BLE001
        detail = None
    return detail or f"The compute manager refused the request ({exc.code})."
