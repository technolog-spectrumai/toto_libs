"""The executor's HTTP surface: a handful of verbs, all signed, over AF_UNIX.

``http.server`` rather than a framework. The API is eleven routes with no
templates, no sessions, no ORM and no static files; a framework would be more
code to audit than the thing it serves, and this process is the one that can
run other people's code.

A UNIX SOCKET, not a TCP port, since 2026-09-10. The socket is a filesystem
object under /run/anastasia that only root and the ``anastasia`` group can
open, so the executor is unreachable from the network however the host is
configured. That replaces a port that was bound on an internal docker network,
which was secrecy by deployment convention rather than by permission.

TWO CHECKS, and only one of them is authentication:

* **HMAC over the body** (see ``protocol``) is the real one, and it is
  unchanged. A failure answers a fixed 401 sentence and logs the real reason —
  a verification error that explains WHICH half was wrong turns the endpoint
  into an oracle.
* **SO_PEERCRED** is NOT authentication here and must not be mistaken for it.
  The app containers run as root (no ``USER`` in the image), so the kernel
  reports uid 0 for every caller — which is also every root process on the
  host. What it buys is a cheap refusal of unprivileged local users and an
  honest audit line naming the calling pid and its cgroup.

Django-free.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..families import ParamError
from ..families import operation as operation_for
from ..limits import Limits, LimitsError
from . import capsules as capsules_mod
from . import control, pressure, protocol, reconcile, telemetry
from .drivers import DriverError
from .staging import StagingError

log = logging.getLogger("toto.anastasia.executor.service")

MAX_BODY_BYTES = 512 * 1024 * 1024

_UUID = r"[0-9a-fA-F-]{8,40}"
ROUTES = [
    ("POST", re.compile(rf"^/capsules/({_UUID})/mount$"), "mount"),
    ("POST", re.compile(rf"^/capsules/({_UUID})/unmount$"), "unmount"),
    ("GET", re.compile(rf"^/capsules/({_UUID})/status$"), "capsule_status"),
    # Separate from status because it costs a filesystem walk: status is
    # polled every few seconds, this is asked for.
    ("GET", re.compile(rf"^/capsules/({_UUID})/storage$"), "capsule_storage"),
    ("POST", re.compile(r"^/jobs$"), "start_execution"),
    ("GET", re.compile(rf"^/jobs/({_UUID})$"), "execution_status"),
    ("GET", re.compile(rf"^/jobs/({_UUID})/out$"), "execution_output"),
    ("POST", re.compile(rf"^/jobs/({_UUID})/kill$"), "kill_execution"),
    ("POST", re.compile(rf"^/jobs/({_UUID})/finish$"), "finish_execution"),
    ("GET", re.compile(r"^/pool$"), "pool"),
    ("POST", re.compile(r"^/reconcile$"), "reconcile"),
    ("GET", re.compile(r"^/health$"), "health"),
    # Admission control. POST because each one CHANGES the machine, and a
    # GET that drained a host would be reachable from a browser prefetch.
    ("POST", re.compile(r"^/control/drain$"), "drain"),
    ("POST", re.compile(r"^/control/resume$"), "resume"),
    ("POST", re.compile(r"^/control/stop$"), "stop"),
    ("GET", re.compile(r"^/control$"), "control_status"),
    ("GET", re.compile(r"^/metrics$"), "metrics"),
]


class Api:
    """The verbs, separated from the HTTP so they can be tested without a socket."""

    def __init__(self, manager: capsules_mod.CapsuleManager):
        self.manager = manager
        self._lock = threading.Lock()

    # Every mutating verb takes one lock. Admission, mounting and execution
    # start all read-then-write shared state (cgroups, directories, container
    # lists), and the manager is the single serialisation point by design —
    # the same reason the Django side locks a PoolGuard row.
    def _locked(self, fn, *args, **kwargs):
        with self._lock:
            return fn(*args, **kwargs)

    def mount(self, capsule, payload):
        # The OPERATOR's switch first, then the machine's. Both refuse, and
        # both refuse with 503, but they are different sentences: pressure is
        # temporary and self-clearing ("try again shortly"), while a drain or
        # a stop is a person's decision and stays until a person reverses it.
        # Telling somebody to retry into a stopped host is how a refusal
        # becomes a support ticket.
        admission = control.read(self.manager.staging_root)
        if admission["state"] != control.OPEN:
            return 503, {"error": control.refusal(admission),
                         "admission": admission}
        state = pressure.report(self.manager.staging_root)
        if not state["admitting"]:
            return 503, {"error": pressure.refusal(state), "pressure": state}
        limits = Limits.from_mapping(payload.get("limits"))
        return 200, self._locked(self.manager.mount, capsule, limits)

    def unmount(self, capsule, payload):
        return 200, self._locked(self.manager.unmount, capsule)

    def capsule_status(self, capsule, payload):
        return 200, self.manager.status(capsule)

    def capsule_storage(self, capsule, payload):
        """Bytes and file counts. Never a name — see `executor/storage.py`."""
        return 200, self.manager.storage(capsule)

    def start_execution(self, payload):
        # Draining means "no NEW work", and a job is new work even inside a
        # Capsule that is already mounted. Checking only at mount would let a
        # mounted Capsule keep starting jobs through the whole drain, which is
        # exactly the thing an operator draining for a reboot is trying to
        # stop.
        admission = control.read(self.manager.staging_root)
        if admission["state"] != control.OPEN:
            return 503, {"error": control.refusal(admission),
                         "admission": admission}
        capsule = str(payload.get("capsule") or "")
        execution = str(payload.get("execution") or "")
        if not capsule or not execution:
            return 400, {"error": "capsule and execution are required"}

        op = operation_for(str(payload.get("operation") or ""))
        params = op.clean(payload.get("params"))
        timeout = op.clean_timeout(payload.get("timeout"))
        limits = Limits.from_mapping(payload.get("limits"))

        blob = payload.get("payload_b64") or ""
        try:
            body = base64.b64decode(blob) if blob else b""
        except (ValueError, TypeError):
            return 400, {"error": "payload_b64 is not valid base64"}

        result = self._locked(
            self.manager.start_execution, capsule=capsule, execution=execution,
            operation=op.name, params=params, limits=limits, timeout=timeout,
            payload=body)
        return 200, result

    def execution_status(self, execution, payload):
        capsule = str(payload.get("capsule") or "")
        return 200, self.manager.execution_status(capsule=capsule, execution=execution)

    def execution_output(self, execution, payload):
        capsule = str(payload.get("capsule") or "")
        blob = self.manager.collect(capsule=capsule, execution=execution)
        return 200, {"tar_b64": base64.b64encode(blob).decode("ascii"),
                     "bytes": len(blob)}

    def kill_execution(self, execution, payload):
        capsule = str(payload.get("capsule") or "")
        return 200, self._locked(self.manager.kill_execution, capsule=capsule,
                                 execution=execution)

    def finish_execution(self, execution, payload):
        capsule = str(payload.get("capsule") or "")
        return 200, self._locked(self.manager.finish_execution, capsule=capsule,
                                 execution=execution)

    def pool(self, payload):
        state = pressure.report(self.manager.staging_root)
        return 200, {
            "generation": self.manager.generation,
            "slice_driver": self.manager.slices.describe(),
            "pressure": state,
            "adopted": reconcile.adopt(self.manager),
        }

    def reconcile(self, payload):
        known = payload.get("known_capsules")
        return 200, self._locked(reconcile.tick, self.manager, known)

    def drain(self, payload):
        """Stop taking new work; let running work finish.

        What an operator sets before a reboot or an upgrade. Nothing running is
        touched, so the machine empties itself at the speed of its longest job.
        """
        state = control.write(self.manager.staging_root, control.DRAINING,
                              reason=str(payload.get("reason") or "")[:400])
        return 200, {"admission": state}

    def resume(self, payload):
        """Take work again. The only way out of drain OR stop."""
        state = control.write(self.manager.staging_root, control.OPEN,
                              reason=str(payload.get("reason") or "")[:400])
        return 200, {"admission": state}

    def stop(self, payload):
        """THE EMERGENCY. No new work, and every running job killed.

        Throws away work on purpose, which is why it is its own verb rather
        than a flag on drain: an operator typing this has decided that what is
        running is the problem. The count of what was destroyed comes back, so
        the decision has a number attached to it in the log.
        """
        state = control.write(self.manager.staging_root, control.STOPPED,
                              reason=str(payload.get("reason") or "")[:400])
        killed = self._locked(reconcile.kill_everything, self.manager)
        return 200, {"admission": state, "runners_destroyed": killed}

    def metrics(self, payload):
        """Prometheus text. Still HMAC-signed, like every other route.

        Unusual for a metrics endpoint — most are left open on the assumption
        that they leak nothing. This one is not left open for two reasons: it
        is on a socket only root can open anyway, so signing costs nothing, and
        an unauthenticated route on THIS process would be the only one, which
        is exactly the sort of exception that outlives the reason for it.

        The response is a STRING, not a dict — the one route here that is not
        JSON. `_reply_text` below carries it, because a scraper reading
        `{"metrics": "..."}` would have to unwrap it and none do.
        """
        return 200, telemetry.render(
            self.manager,
            admission=control.read(self.manager.staging_root),
            pressure_report=pressure.report(self.manager.staging_root))

    def control_status(self, payload):
        return 200, {"admission": control.read(self.manager.staging_root)}

    def health(self, payload):
        """Alive, which generation, and — since 2026-09-10 — WHICH ISOLATION.

        The tier is read off the live driver rather than off a setting, and the
        difference is the whole point: a setting says what somebody asked for,
        the driver says what the next job will actually get. Those disagree
        exactly when it matters most.
        """
        return 200, {"ok": True, "generation": self.manager.generation,
                     "docker": self.manager.docker.available(),
                     "tier": self.manager.docker.name,
                     "admission": control.read(self.manager.staging_root)}


def make_handler(api: Api, secret: str):
    nonces = protocol.NonceCache()

    class Handler(BaseHTTPRequestHandler):
        server_version = "anastasia"
        # Never echo the client's identity or the server's Python version.
        sys_version = ""

        def log_message(self, fmt, *args):
            log.info("anastasia-executor: " + fmt, *args)

        def _reply(self, status: int, payload: dict):
            body = protocol.encode(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _reply_text(self, status: int, body: str):
            raw = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type",
                             "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _handle(self, method: str):
            path = self.path.split("?", 1)[0]
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._reply(400, {"error": "bad Content-Length"})
            if length > MAX_BODY_BYTES:
                return self._reply(413, {"error": "request body is too large"})
            body = self.rfile.read(length) if length else b""

            try:
                protocol.verify(secret=secret, method=method, path=path,
                                body=body, headers=self.headers, nonces=nonces)
            except protocol.SignatureError as exc:
                # The reason goes to the log; the caller gets one sentence.
                log.warning("anastasia-executor: refused %s %s — %s",
                            method, path, exc)
                return self._reply(401, {"error": "request is not authenticated"})

            for verb, pattern, name in ROUTES:
                if verb != method:
                    continue
                match = pattern.match(path)
                if not match:
                    continue
                try:
                    payload = protocol.decode(body) if body else {}
                    if not isinstance(payload, dict):
                        raise ValueError("request body must be a JSON object")
                    status, result = getattr(api, name)(*match.groups(), payload)
                    if isinstance(result, str):
                        # Prometheus text, not JSON. The only route that
                        # answers with a string, and the content type has to
                        # match or every scraper refuses it.
                        return self._reply_text(status, result)
                except (ParamError, LimitsError, ValueError) as exc:
                    return self._reply(400, {"error": str(exc)})
                except StagingError as exc:
                    return self._reply(400, {"error": str(exc)})
                except capsules_mod.CapsuleError as exc:
                    return self._reply(409, {"error": str(exc)})
                except DriverError as exc:
                    # The BASE, not DockerError: every tier's failures must
                    # reach a caller the same way, and naming one runtime here
                    # is how the next tier's errors become a 500.
                    log.exception("anastasia-executor: the runtime refused")
                    return self._reply(502, {"error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    log.exception("anastasia-executor: %s failed", name)
                    return self._reply(500, {"error": f"{type(exc).__name__}"})
                return self._reply(status, result)

            return self._reply(404, {"error": "no such route"})

        def do_GET(self):     # noqa: N802 - BaseHTTPRequestHandler's contract
            self._handle("GET")

        def do_POST(self):    # noqa: N802
            self._handle("POST")

    return Handler


#: uids allowed to open the socket at all. Root only by default: the app
#: containers run as root, so this is what their calls arrive as. Widened by
#: the operator when the image grows a USER, never by a caller.
DEFAULT_PEER_UIDS = frozenset({0})


def _peer_credentials(sock) -> tuple[int, int, int]:
    """(pid, uid, gid) of whoever opened this connection, from the KERNEL.

    ``SO_PEERCRED`` is filled in by the kernel at connect time and cannot be
    forged by the peer, which is what separates it from anything in a header.
    Three native ints — ``struct ucred`` — and the size is fixed on Linux.
    """
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                          struct.calcsize("3i"))
    return struct.unpack("3i", raw)


def _peer_cgroup(pid: int) -> str:
    """Which container the caller is in, for the audit line only.

    Best-effort and never a gate: a pid can be recycled between the connection
    and this read, so what it says is "probably this container" — useful in a
    log, worthless as a permission.
    """
    try:
        with open(f"/proc/{pid}/cgroup", "r", encoding="utf-8") as handle:
            return handle.read().strip().splitlines()[-1][:200]
    except OSError:
        return ""


class UnixHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer over AF_UNIX, with a peer check at accept time.

    ``allow_reuse_address`` is meaningless for a unix socket and actively
    misleading — the reuse problem is a stale FILE, not a TIME_WAIT port — so
    it is off and ``server_bind`` unlinks the path instead.
    """

    address_family = socket.AF_UNIX
    allow_reuse_address = False

    #: Set by ``serve``. A frozenset, so a handler cannot mutate it.
    allowed_uids = DEFAULT_PEER_UIDS

    def server_bind(self):
        """Bind at 0660, atomically, and never inherit a stale socket.

        The mode is set with ``umask`` around the bind rather than a chmod
        afterwards: between bind and chmod the socket would be world-writable,
        and that window is exactly when a deploy is running as root with
        everything else already up.
        """
        path = self.server_address
        # A leftover from a killed process: connect() would fail with
        # ECONNREFUSED forever, and bind() with EADDRINUSE. Unlink only a
        # SOCKET — never a regular file somebody put there by mistake.
        try:
            if os.path.exists(path) and __import__("stat").S_ISSOCK(
                    os.stat(path).st_mode):
                os.unlink(path)
        except OSError:
            pass
        old_umask = os.umask(0o117)          # 0660 on the socket
        try:
            super().server_bind()
        finally:
            os.umask(old_umask)

    def verify_request(self, request, client_address):
        """Refuse a peer whose uid is not allowed, before any bytes are read.

        NOT the authentication — HMAC is, and it runs per request regardless.
        This is the cheap first gate: an unprivileged local user who somehow
        reached the socket is turned away without the server parsing anything
        they sent.
        """
        try:
            pid, uid, _gid = _peer_credentials(request)
        except OSError:
            log.warning("anastasia-executor: refused a peer with no credentials")
            return False
        if uid not in self.allowed_uids:
            log.warning(
                "anastasia-executor: refused uid %s (pid %s, %s) — allowed: %s",
                uid, pid, _peer_cgroup(pid) or "no cgroup",
                ",".join(str(u) for u in sorted(self.allowed_uids)))
            return False
        return True


def serve(*, socket_path: str, secret: str, manager: capsules_mod.CapsuleManager,
          allowed_uids=DEFAULT_PEER_UIDS) -> UnixHTTPServer:
    """Bind the executor's socket and return the server, unstarted.

    The secret guard is unchanged and stays first: an unauthenticated executor
    is a remote shell, and that is true of a unix socket exactly as it was of a
    port — filesystem permissions decide WHO may knock, never WHAT they may
    ask for.
    """
    if not secret:
        raise RuntimeError(
            "the executor refuses to start without ANASTASIA_SHARED_SECRET — "
            "an unauthenticated executor is a remote shell")
    if not socket_path:
        raise RuntimeError(
            "the executor refuses to start without a socket path "
            "(ANASTASIA_EXECUTOR_SOCKET)")
    parent = os.path.dirname(socket_path)
    if parent and not os.path.isdir(parent):
        raise RuntimeError(
            f"the executor's socket directory does not exist: {parent}. It is "
            "created by the systemd unit (RuntimeDirectory=anastasia); a "
            "missing one means the unit was bypassed.")
    UnixHTTPServer.allowed_uids = frozenset(allowed_uids)
    return UnixHTTPServer(socket_path, make_handler(Api(manager), secret))
