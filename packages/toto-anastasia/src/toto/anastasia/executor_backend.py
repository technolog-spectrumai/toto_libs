"""The Django side of the wire: a RuntimeBackend that calls the executor.

This is the ONLY module in the app that knows the executor exists. Everything
else — services, execute, the views — talks to ``runtime.RuntimeBackend``, so a
host with no executor keeps working (booking is arithmetic) and the tests keep
running with a fake.

A UNIX SOCKET since 2026-09-10, where this used to be an HTTP call to a
container on an internal docker network. ``http.client`` rather than
``requests``: the request is HTTP either way, and all that changes is which
kind of socket carries it — so the transport is thirty lines of
``HTTPConnection`` subclass rather than a dependency that would have to be
taught about AF_UNIX anyway.

The HMAC is unchanged and is still the authentication. Filesystem permissions
decide who may open the socket; the signature decides whether what they send is
honoured.
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import socket

from . import conf
from .executor import protocol
from .limits import Limits
from .runtime import RuntimeBackend, RuntimeUnavailable

log = logging.getLogger("toto.anastasia.executor_backend")

#: Long enough for an executor that is unpacking a staged input, short enough
#: that a wedged executor does not hold a celery worker for the whole job.
#: Starting an execution RETURNS as soon as the container is running — it does
#: not wait for the job — so this bounds setup, not work.
DEFAULT_TIMEOUT = 60


class FilesRefused(Exception):
    """The executor would not do that with that file, and said why.

    Distinct from ``RuntimeUnavailable`` because the two need different
    handling: a refusal ("that name escapes the files area", "already
    exists") is the caller's to show the user and move on from, while an
    unavailable executor is nobody's fault at the desk. Carries the executor's
    own sentence, which is written for a person.
    """


class UnixHTTPConnection(http.client.HTTPConnection):
    """An ordinary HTTP connection whose socket happens to be a file.

    ``http.client`` builds the request, parses the status line and the headers,
    and hands back a file-like response — none of which cares what kind of
    socket it is written to. So the whole of AF_UNIX support is: connect to a
    path instead of a (host, port).

    The Host header still has to be SOMETHING (HTTP/1.1 requires it, and the
    executor signs the path rather than the authority), so the parent is given
    a placeholder hostname. It never resolves it, because ``connect`` is
    overridden before any lookup would happen.
    """

    def __init__(self, socket_path: str, timeout: float = DEFAULT_TIMEOUT):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


class ExecutorRuntimeBackend(RuntimeBackend):
    """Stateless, per the ABC's contract — a fresh one per ``get_backend()``."""

    name = "executor"

    def _call(self, method: str, path: str, payload: dict | None = None,
              *, timeout: int = DEFAULT_TIMEOUT,
              refusal: type[Exception] | None = None) -> dict:
        """One signed request. ``refusal`` is the exception a 4xx becomes.

        Every non-2xx used to be ``RuntimeUnavailable``, and for the verbs
        that existed that was honest enough: a 409 on a job start IS the
        runtime declining. A file transfer is different — "there is no file
        named that" is an answer, not an outage, and reporting it as one
        would tell a user the executor is down when they mistyped a name.
        So a caller that can act on a refusal names the type; 401 is still
        an operator problem and 5xx is still unavailability, whatever is
        passed.
        """
        socket_path = conf.executor_socket()
        if not socket_path:
            raise RuntimeUnavailable(
                "This deployment has no Anastasia executor socket configured "
                "(ANASTASIA_EXECUTOR_SOCKET), so Capsules cannot be mounted.")
        secret = conf.shared_secret()
        if not secret:
            raise RuntimeUnavailable(
                "The Anastasia executor is configured without a shared secret, "
                "so this host cannot sign requests to it.")

        body = protocol.encode(payload or {})
        headers = protocol.sign(secret=secret, method=method, path=path,
                                body=body)
        headers["Content-Type"] = "application/json"

        conn = UnixHTTPConnection(socket_path, timeout=timeout)
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read().decode("utf-8") or "{}"
            if response.status >= 400:
                detail = _error_sentence_from(raw, response.status)
                if response.status == 401:
                    # Never surface "unauthenticated" to a user: it is an
                    # operator problem (a secret mismatch), and nothing they do
                    # will fix it.
                    log.error("anastasia: the executor rejected our signature "
                              "— ANASTASIA_SHARED_SECRET differs between this "
                              "host and the executor")
                    raise RuntimeUnavailable(
                        "This host and the compute executor are not configured "
                        "with the same secret, so it refused the request. An "
                        "administrator needs to look at it.")
                if refusal is not None and response.status < 500:
                    raise refusal(detail)
                raise RuntimeUnavailable(detail)
            return json.loads(raw)
        except FileNotFoundError as exc:
            # The socket path does not exist at all. Distinguished from a
            # refusal because the operator fix differs: this one means the
            # executor was never started, or its directory was not mounted in.
            raise RuntimeUnavailable(
                "The compute executor's socket is not there "
                f"({socket_path}). Your reservation is untouched.") from exc
        except ConnectionError as exc:
            raise RuntimeUnavailable(
                f"The compute executor is not answering ({exc}). Your "
                "reservation is untouched.") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise RuntimeUnavailable(
                f"The compute executor did not answer in time ({exc}).") from exc
        except OSError as exc:
            raise RuntimeUnavailable(
                f"The compute executor could not be reached ({exc}). Your "
                "reservation is untouched.") from exc
        except ValueError as exc:
            raise RuntimeUnavailable(
                f"The compute executor sent something unreadable ({exc}).") from exc
        finally:
            conn.close()

    # -- the RuntimeBackend contract --------------------------------------

    def mount(self, lease) -> dict:
        # `egress` travels with the limits because it is the same kind of fact:
        # what this reservation was granted. The executor refuses the mount
        # outright if it cannot filter the network — so a Capsule whose owner
        # asked for the internet on a host that cannot supply it fails loudly
        # here, rather than mounting and failing at the first fetch with
        # nothing pointing at the cause.
        return self._call("POST", f"/capsules/{lease.uuid}/mount",
                          {"limits": lease.limits.as_dict(),
                           "egress": bool(getattr(lease, "egress", False))})

    def unmount(self, lease, *, purge: bool = False) -> dict:
        """Tear the runtime down; take the files too only on ``purge``.

        ``purge`` is the lifetime rule from `executor/capsules.py` seen from
        this side: the files area survives every unmount but the one that
        ends the reservation, and release is the only caller that says so.
        Sent explicitly rather than defaulted on the executor, so a reader of
        the wire sees the decision.
        """
        try:
            return self._call("POST", f"/capsules/{lease.uuid}/unmount",
                              {"purge": bool(purge)})
        except RuntimeUnavailable:
            # Teardown must never be blockable by an absent executor: with
            # the executor gone, so are its containers, and refusing here would
            # strand the lease. Reconciliation cleans up if it comes back —
            # including, for a purge, the files: the sweeper removes the whole
            # directory of any capsule the app no longer lists.
            log.warning("anastasia: unmounting %s with no executor answering",
                        lease.uuid)
            return {"unmounted": True, "runners_destroyed": 0,
                    "executor_absent": True}

    def status(self, lease) -> dict:
        try:
            return self._call("GET", f"/capsules/{lease.uuid}/status", timeout=15)
        except RuntimeUnavailable:
            return {}

    def storage(self, lease) -> dict:
        """Bytes and file counts for one capsule. NEVER a filename.

        A longer timeout than `status` because this walks a tree, and an empty
        dict when the runtime cannot answer — a storage reading is information,
        not a precondition, so a missing one must not fail the page that shows
        everything else.
        """
        try:
            return self._call("GET", f"/capsules/{lease.uuid}/storage",
                              timeout=30)
        except RuntimeUnavailable:
            return {}

    # -- the files area ------------------------------------------------------
    #
    # Beyond the RuntimeBackend contract, like the operator verbs below: the
    # null backend has no files area and must not have to pretend to one.
    # Bytes go base64 in the JSON body both ways, like a job's payload and its
    # output tar; the NAME goes in the body too, never in the path, because
    # the executor signs and routes on the path with the query string already
    # stripped.

    def capsule_files(self, lease) -> dict:
        """The listing. ``{}`` when the runtime cannot answer, like `storage`:
        a listing is information the page shows, not a precondition for it,
        and an empty dict — rather than an empty list — is how the page tells
        "nothing there" from "nobody answered"."""
        try:
            return self._call("GET", f"/capsules/{lease.uuid}/files",
                              timeout=30)
        except RuntimeUnavailable:
            return {}

    def capsule_file_read(self, lease, name: str) -> bytes:
        """One file's bytes. Raises `FilesRefused` with the executor's sentence
        when the name is refused or absent, `RuntimeUnavailable` when nobody
        answered. The same timeout as `collect`: bytes are moving."""
        result = self._call("POST", f"/capsules/{lease.uuid}/files/get",
                            {"name": name}, timeout=120, refusal=FilesRefused)
        return base64.b64decode(result.get("data_b64") or "")

    def capsule_file_write(self, lease, name: str, data: bytes,
                           replace: bool = False) -> dict:
        return self._call("POST", f"/capsules/{lease.uuid}/files/put",
                          {"name": name, "replace": bool(replace),
                           "data_b64": base64.b64encode(data).decode("ascii")},
                          timeout=120, refusal=FilesRefused)

    def capsule_file_delete(self, lease, name: str) -> dict:
        return self._call("POST", f"/capsules/{lease.uuid}/files/delete",
                          {"name": name}, refusal=FilesRefused)

    def start_execution(self, execution, *, params, payload) -> dict:
        body = {
            "capsule": str(execution.lease.uuid),
            "execution": str(execution.uuid),
            "operation": execution.operation,
            "params": params,
            "timeout": execution.timeout_seconds,
            "limits": execution.limits.as_dict(),
        }
        if payload:
            body["payload_b64"] = base64.b64encode(payload).decode("ascii")
        return self._call("POST", "/jobs", body)

    def kill_execution(self, execution) -> dict:
        return self._call("POST", f"/jobs/{execution.uuid}/kill",
                          {"capsule": str(execution.lease.uuid)})

    # -- beyond the ABC: what the caller needs to finish a job -------------

    def execution_status(self, execution) -> dict:
        return self._call("GET", f"/jobs/{execution.uuid}",
                          {"capsule": str(execution.lease.uuid)}, timeout=15)

    def execution_logs(self, execution, offset: int = 0) -> dict:
        """A slice of a running job's output. Never raises.

        An empty slice at the caller's own offset when the runtime cannot
        answer, for the same reason `storage` returns an empty dict: a
        progress console that 500s is worse than one that shows nothing new,
        and the caller polls again in a second either way.
        """
        try:
            return self._call(
                "GET", f"/jobs/{execution.uuid}/logs",
                {"capsule": str(execution.lease.uuid), "offset": offset})
        except Exception:                        # noqa: BLE001
            log.warning("anastasia: could not read logs for %s",
                        execution.uuid, exc_info=True)
            return {"text": "", "offset": offset, "complete": False,
                    "found": False}

    def collect(self, execution) -> bytes:
        result = self._call("GET", f"/jobs/{execution.uuid}/out",
                            {"capsule": str(execution.lease.uuid)}, timeout=120)
        return base64.b64decode(result.get("tar_b64") or "")

    def finish_execution(self, execution) -> dict:
        return self._call("POST", f"/jobs/{execution.uuid}/finish",
                          {"capsule": str(execution.lease.uuid)})

    def pool(self) -> dict:
        return self._call("GET", "/pool", timeout=15)

    def reconcile(self, known_capsules) -> dict:
        return self._call("POST", "/reconcile",
                          {"known_capsules": [str(g) for g in known_capsules]},
                          timeout=120)

    # -- admission control, for an operator ---------------------------------
    #
    # Beyond the RuntimeBackend contract on purpose. Nothing in the request
    # path calls these — they are what a staff page and `executorctl` reach
    # for, and putting them on the ABC would suggest every backend must be
    # drainable when the null one has nothing to drain.

    def admission(self) -> dict:
        """Whether this host is taking work, and why not if it is not."""
        return self._call("GET", "/control", timeout=15)

    def health(self) -> dict:
        """Alive, which generation, which isolation tier. For the staff page."""
        return self._call("GET", "/health", timeout=15)

    def drain(self, reason: str = "") -> dict:
        """Stop taking new work; let running work finish."""
        return self._call("POST", "/control/drain", {"reason": reason})

    def resume(self, reason: str = "") -> dict:
        """Take work again — the only way out of drain or stop."""
        return self._call("POST", "/control/resume", {"reason": reason})

    def emergency_stop(self, reason: str = "") -> dict:
        """No new work, and every running job killed. Destroys work."""
        return self._call("POST", "/control/stop", {"reason": reason},
                          timeout=120)


def _error_sentence_from(raw: str, status: int) -> str:
    """The executor's own sentence where it sent one, a fallback where not.

    The executor answers every refusal with ``{"error": "..."}`` written for a
    person. Preferring it over a status code is what lets a 409 say "this Capsule
    is full" instead of "conflict".
    """
    try:
        detail = json.loads(raw or "{}").get("error")
    except Exception:  # noqa: BLE001
        detail = None
    return detail or f"The compute executor refused the request ({status})."
