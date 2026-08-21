"""The manager's HTTP surface: a handful of verbs, all signed.

``http.server`` rather than a framework. The API is nine routes with no
templates, no sessions, no ORM and no static files; a framework would be more
code to audit than the thing it serves, and this process is the one that holds
the Docker socket.

Every route is authenticated by HMAC over the body (see ``protocol``). A
failure answers a fixed 401 sentence and logs the real reason: a verification
error that explains WHICH half was wrong turns the endpoint into an oracle.

Django-free.
"""

from __future__ import annotations

import base64
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..families import ParamError
from ..families import operation as operation_for
from ..limits import Limits, LimitsError
from . import gears as gears_mod
from . import pressure, protocol, reconcile
from .containers import DockerError
from .staging import StagingError

log = logging.getLogger("toto.anastasia.manager.service")

MAX_BODY_BYTES = 512 * 1024 * 1024

_UUID = r"[0-9a-fA-F-]{8,40}"
ROUTES = [
    ("POST", re.compile(rf"^/gears/({_UUID})/mount$"), "mount"),
    ("POST", re.compile(rf"^/gears/({_UUID})/unmount$"), "unmount"),
    ("GET", re.compile(rf"^/gears/({_UUID})/status$"), "gear_status"),
    ("POST", re.compile(r"^/executions$"), "start_execution"),
    ("GET", re.compile(rf"^/executions/({_UUID})$"), "execution_status"),
    ("GET", re.compile(rf"^/executions/({_UUID})/out$"), "execution_output"),
    ("POST", re.compile(rf"^/executions/({_UUID})/kill$"), "kill_execution"),
    ("POST", re.compile(rf"^/executions/({_UUID})/finish$"), "finish_execution"),
    ("GET", re.compile(r"^/pool$"), "pool"),
    ("POST", re.compile(r"^/reconcile$"), "reconcile"),
    ("GET", re.compile(r"^/health$"), "health"),
]


class Api:
    """The verbs, separated from the HTTP so they can be tested without a socket."""

    def __init__(self, manager: gears_mod.GearManager):
        self.manager = manager
        self._lock = threading.Lock()

    # Every mutating verb takes one lock. Admission, mounting and execution
    # start all read-then-write shared state (cgroups, directories, container
    # lists), and the manager is the single serialisation point by design —
    # the same reason the Django side locks a PoolGuard row.
    def _locked(self, fn, *args, **kwargs):
        with self._lock:
            return fn(*args, **kwargs)

    def mount(self, gear, payload):
        state = pressure.report(self.manager.staging_root)
        if not state["admitting"]:
            return 503, {"error": pressure.refusal(state), "pressure": state}
        limits = Limits.from_mapping(payload.get("limits"))
        return 200, self._locked(self.manager.mount, gear, limits)

    def unmount(self, gear, payload):
        return 200, self._locked(self.manager.unmount, gear)

    def gear_status(self, gear, payload):
        return 200, self.manager.status(gear)

    def start_execution(self, payload):
        gear = str(payload.get("gear") or "")
        execution = str(payload.get("execution") or "")
        if not gear or not execution:
            return 400, {"error": "gear and execution are required"}

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
            self.manager.start_execution, gear=gear, execution=execution,
            operation=op.name, params=params, limits=limits, timeout=timeout,
            payload=body, warm=bool(payload.get("warm")))
        return 200, result

    def execution_status(self, execution, payload):
        gear = str(payload.get("gear") or "")
        return 200, self.manager.execution_status(gear=gear, execution=execution)

    def execution_output(self, execution, payload):
        gear = str(payload.get("gear") or "")
        blob = self.manager.collect(gear=gear, execution=execution)
        return 200, {"tar_b64": base64.b64encode(blob).decode("ascii"),
                     "bytes": len(blob)}

    def kill_execution(self, execution, payload):
        gear = str(payload.get("gear") or "")
        return 200, self._locked(self.manager.kill_execution, gear=gear,
                                 execution=execution)

    def finish_execution(self, execution, payload):
        gear = str(payload.get("gear") or "")
        return 200, self._locked(self.manager.finish_execution, gear=gear,
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
        known = payload.get("known_gears")
        return 200, self._locked(reconcile.tick, self.manager, known)

    def health(self, payload):
        return 200, {"ok": True, "generation": self.manager.generation,
                     "docker": self.manager.docker.available()}


def make_handler(api: Api, secret: str):
    nonces = protocol.NonceCache()

    class Handler(BaseHTTPRequestHandler):
        server_version = "anastasia"
        # Never echo the client's identity or the server's Python version.
        sys_version = ""

        def log_message(self, fmt, *args):
            log.info("anastasia-manager: " + fmt, *args)

        def _reply(self, status: int, payload: dict):
            body = protocol.encode(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
                log.warning("anastasia-manager: refused %s %s — %s",
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
                except (ParamError, LimitsError, ValueError) as exc:
                    return self._reply(400, {"error": str(exc)})
                except StagingError as exc:
                    return self._reply(400, {"error": str(exc)})
                except gears_mod.GearError as exc:
                    return self._reply(409, {"error": str(exc)})
                except DockerError as exc:
                    log.exception("anastasia-manager: docker refused")
                    return self._reply(502, {"error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    log.exception("anastasia-manager: %s failed", name)
                    return self._reply(500, {"error": f"{type(exc).__name__}"})
                return self._reply(status, result)

            return self._reply(404, {"error": "no such route"})

        def do_GET(self):     # noqa: N802 - BaseHTTPRequestHandler's contract
            self._handle("GET")

        def do_POST(self):    # noqa: N802
            self._handle("POST")

    return Handler


def serve(*, host: str, port: int, secret: str,
          manager: gears_mod.GearManager) -> ThreadingHTTPServer:
    if not secret:
        raise RuntimeError(
            "the manager refuses to start without ANASTASIA_SHARED_SECRET — an "
            "unauthenticated Docker manager is a remote shell")
    httpd = ThreadingHTTPServer((host, port), make_handler(Api(manager), secret))
    return httpd
