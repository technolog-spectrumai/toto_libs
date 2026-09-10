"""The manager's HTTP surface, over a real socket.

A real server on a real loopback port rather than calling the handler
in-process: the things most worth testing here — that an unsigned request is
refused, that a body over the cap is rejected — live in the HTTP layer, and
mocking it would test the mock.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import tempfile
from unittest import mock
import threading
import urllib.error
import urllib.request
import uuid

from django.test import SimpleTestCase

from toto.anastasia.limits import Limits
from toto.anastasia.executor import control, capsules, protocol, service
from toto.anastasia.executor_backend import UnixHTTPConnection

from .fakes import CountingSliceDriver, FakeDocker

SECRET = "executor-test-secret"


class ServiceTestCase(SimpleTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="anastasia-svc-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        # PIN THE IMAGE LOCK TO A PATH THIS TEST OWNS.
        #
        # `images.load()` falls back to /etc/anastasia/images.lock.json, which
        # is a REAL FILE on any host with a deployment installed. A fake driver
        # reports fake digests, so they can never match a real lock, and four
        # tests here failed with a 409 the moment this machine had one — while
        # passing on any box that had never deployed. A suite whose result
        # depends on whether the host is a deployment target is not a suite.
        #
        # The path deliberately does not exist: an absent lock means UNPINNED,
        # which is the documented, allowed state and the one these tests want.
        _lock = mock.patch.dict(
            os.environ,
            {"ANASTASIA_IMAGE_LOCK": os.path.join(self.root, "images.lock.json")})
        _lock.start()
        self.addCleanup(_lock.stop)
        self.docker = FakeDocker()
        self.manager = capsules.CapsuleManager(
            staging_root=self.root, slice_driver=CountingSliceDriver(),
            docker=self.docker, generation="gen-http")
        # A REAL unix socket, in a directory this test owns. The whole point
        # of the transport is that it is a filesystem object, so faking it
        # would leave the one thing that changed untested.
        self.socket_path = os.path.join(self.root, "executord.sock")
        # The suite runs as an ordinary user; in production every caller is
        # container-root. Passing this process's own uid is what lets the test
        # exercise the real gate rather than disable it — and
        # PeerCredentialTests below asserts that a uid NOT in this set is
        # refused before a single byte is parsed.
        self.httpd = service.serve(socket_path=self.socket_path, secret=SECRET,
                                   manager=self.manager,
                                   allowed_uids={os.getuid()})
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.shutdown)
        self.capsule = str(uuid.uuid4())

    def call(self, method, path, payload=None, *, secret=SECRET, headers=None):
        body = protocol.encode(payload or {})
        sent = protocol.sign(secret=secret, method=method, path=path, body=body)
        sent["Content-Type"] = "application/json"
        sent.update(headers or {})
        conn = UnixHTTPConnection(self.socket_path, timeout=10)
        try:
            conn.request(method, path, body=body, headers=sent)
            response = conn.getresponse()
            raw = response.read() or b"{}"
            return response.status, json.loads(raw)
        finally:
            conn.close()


class AuthenticationTests(ServiceTestCase):
    def test_a_signed_request_is_served(self):
        status, body = self.call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["generation"], "gen-http")

    def test_an_unsigned_request_is_refused(self):
        """No signature at all, not merely a wrong one.

        Sent over the socket by hand rather than through `call`, which always
        signs. Reaching the socket is not permission to be served: the peer
        check decides who may knock and HMAC decides what is honoured.
        """
        conn = UnixHTTPConnection(self.socket_path, timeout=10)
        try:
            conn.request("GET", "/health", headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 401)
        finally:
            conn.close()

    def test_a_request_signed_with_the_wrong_secret_is_refused(self):
        status, _ = self.call("GET", "/health", secret="not-the-secret")
        self.assertEqual(status, 401)

    def test_the_refusal_does_not_say_which_half_was_wrong(self):
        """A verification error that explains itself is an oracle."""
        status, body = self.call("GET", "/health", secret="wrong")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "request is not authenticated")

    def test_a_replayed_request_is_refused_the_second_time(self):
        body = protocol.encode({})
        sent = protocol.sign(secret=SECRET, method="GET", path="/health",
                             body=body)
        sent["Content-Type"] = "application/json"

        def fire():
            """The SAME signed headers twice — a captured request, replayed."""
            conn = UnixHTTPConnection(self.socket_path, timeout=10)
            try:
                conn.request("GET", "/health", body=body, headers=sent)
                response = conn.getresponse()
                response.read()
                return response.status
            finally:
                conn.close()

        self.assertEqual(fire(), 200)
        # The nonce cache still earns its keep over a unix socket: the peer
        # check says WHO may connect, and says nothing about whether this exact
        # request has been seen before.
        self.assertEqual(fire(), 401)

    def test_an_unknown_route_is_a_404_but_only_after_authenticating(self):
        status, _ = self.call("GET", "/secrets", secret="wrong")
        self.assertEqual(status, 401)
        status, _ = self.call("GET", "/secrets")
        self.assertEqual(status, 404)


class RouteTests(ServiceTestCase):
    def mount(self):
        return self.call("POST", f"/capsules/{self.capsule}/mount",
                         {"limits": Limits(2000, 1024, 512, 256).as_dict()})

    def test_mount_then_execute_then_collect_then_unmount(self):
        status, body = self.mount()
        self.assertEqual(status, 200)
        self.assertEqual(body["manager_generation"], "gen-http")

        execution = str(uuid.uuid4())
        status, body = self.call("POST", "/jobs", {
            "capsule": self.capsule, "execution": execution,
            "operation": "render_pdf",
            "limits": Limits(1000, 512, 256, 64).as_dict(),
        })
        self.assertEqual(status, 200)

        status, body = self.call("GET", f"/jobs/{execution}",
                                 {"capsule": self.capsule})
        self.assertEqual(status, 200)
        self.assertTrue(body["running"])

        import os
        out = os.path.join(self.manager.exec_dir(self.capsule, execution), "out")
        with open(os.path.join(out, "output.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4")
        status, body = self.call("GET", f"/jobs/{execution}/out",
                                 {"capsule": self.capsule})
        self.assertEqual(status, 200)
        self.assertTrue(base64.b64decode(body["tar_b64"]))

        status, body = self.call("POST", f"/capsules/{self.capsule}/unmount")
        self.assertEqual(status, 200)
        self.assertEqual(body["runners_destroyed"], 1)

    def test_a_caller_cannot_smuggle_docker_parameters(self):
        """The API has no word for an image, a mount or a command — so these
        are refused as unknown parameters rather than filtered out."""
        self.mount()
        for hostile in ({"image": "alpine"}, {"command": "sh"},
                        {"privileged": True}, {"volumes": ["/:/host"]},
                        {"cap_add": ["SYS_ADMIN"]}, {"network": "host"}):
            with self.subTest(param=next(iter(hostile))):
                status, body = self.call("POST", "/jobs", {
                    "capsule": self.capsule, "execution": str(uuid.uuid4()),
                    "operation": "render_pdf", "params": hostile})
                self.assertEqual(status, 400)
                self.assertIn("does not take", body["error"])

    def test_an_unknown_operation_is_a_400_not_a_500(self):
        self.mount()
        status, body = self.call("POST", "/jobs", {
            "capsule": self.capsule, "execution": str(uuid.uuid4()),
            "operation": "docker_run"})
        self.assertEqual(status, 400)
        self.assertIn("not an Anastasia operation", body["error"])

    def test_a_hostile_payload_is_a_400(self):
        import io
        import tarfile
        self.mount()
        evil = io.BytesIO()
        with tarfile.open(fileobj=evil, mode="w") as archive:
            info = tarfile.TarInfo("../escaped")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        status, body = self.call("POST", "/jobs", {
            "capsule": self.capsule, "execution": str(uuid.uuid4()),
            "operation": "render_pdf",
            "payload_b64": base64.b64encode(evil.getvalue()).decode()})
        self.assertEqual(status, 400)
        self.assertIn("escapes", body["error"])

    def test_bad_limits_are_a_400(self):
        self.mount()
        status, body = self.call("POST", "/jobs", {
            "capsule": self.capsule, "execution": str(uuid.uuid4()),
            "operation": "render_pdf", "limits": {"gpus": 8}})
        self.assertEqual(status, 400)
        self.assertIn("gpus", body["error"])

    def test_a_missing_runner_image_is_a_409(self):
        self.mount()
        self.docker.images.discard("anastasia-pdf")
        status, body = self.call("POST", "/jobs", {
            "capsule": self.capsule, "execution": str(uuid.uuid4()),
            "operation": "render_pdf"})
        self.assertEqual(status, 409)
        self.assertIn("runner image", body["error"])

    def test_pool_reports_the_ceiling_and_the_pressure(self):
        status, body = self.call("GET", "/pool")
        self.assertEqual(status, 200)
        self.assertIn("slice_driver", body)
        self.assertIn("admitting", body["pressure"])

    def test_reconcile_takes_the_callers_capsule_list(self):
        self.mount()
        self.call("POST", "/jobs", {
            "capsule": self.capsule, "execution": str(uuid.uuid4()),
            "operation": "render_pdf"})
        status, body = self.call("POST", "/reconcile", {"known_capsules": []})
        self.assertEqual(status, 200)
        self.assertEqual(body["orphans_destroyed"], 1)


class RefusalTests(ServiceTestCase):
    def test_the_executor_will_not_start_without_a_secret(self):
        """An unauthenticated executor is a remote shell."""
        with self.assertRaises(RuntimeError) as caught:
            service.serve(socket_path=os.path.join(self.root, "x.sock"),
                          secret="", manager=self.manager)
        self.assertIn("remote shell", str(caught.exception))

    def test_the_executor_will_not_start_without_a_socket_path(self):
        with self.assertRaises(RuntimeError) as caught:
            service.serve(socket_path="", secret=SECRET, manager=self.manager)
        self.assertIn("ANASTASIA_EXECUTOR_SOCKET", str(caught.exception))

    def test_a_missing_socket_directory_is_refused_by_name(self):
        """The unit creates it (RuntimeDirectory=); a missing one means the
        unit was bypassed, and saying so beats a bare ENOENT."""
        with self.assertRaises(RuntimeError) as caught:
            service.serve(socket_path=os.path.join(self.root, "nope", "x.sock"),
                          secret=SECRET, manager=self.manager)
        self.assertIn("socket directory does not exist", str(caught.exception))

    def test_the_socket_is_not_world_writable(self):
        """0660 root:anastasia, set with umask AROUND the bind.

        A chmod after bind would leave a window where anything on the host
        could connect, and that window is exactly when a deploy is running.
        """
        mode = os.stat(self.socket_path).st_mode
        self.assertFalse(mode & stat.S_IWOTH, oct(mode))
        self.assertFalse(mode & stat.S_IROTH, oct(mode))

    def test_a_stale_socket_file_is_replaced_rather_than_fatal(self):
        """A killed executor leaves the file behind; bind would EADDRINUSE."""
        path = os.path.join(self.root, "stale.sock")
        first = service.serve(socket_path=path, secret=SECRET,
                              manager=self.manager,
                              allowed_uids={os.getuid()})
        first.server_close()
        self.assertTrue(os.path.exists(path))
        second = service.serve(socket_path=path, secret=SECRET,
                               manager=self.manager,
                               allowed_uids={os.getuid()})
        second.server_close()


class PeerCredentialTests(ServiceTestCase):
    """SO_PEERCRED is a gate, not authentication — but it must actually gate.

    The distinction matters and is easy to lose: the app containers run as
    root, so in production this check passes for every legitimate caller and
    HMAC is what decides anything. What it buys is that an unprivileged local
    user who reaches the socket is turned away before the server parses what
    they sent.

    Tested by binding a second server that allows NOBODY, which is the only way
    to make this process fail its own check.
    """

    def test_a_uid_outside_the_allowlist_is_refused(self):
        path = os.path.join(self.root, "closed.sock")
        httpd = service.serve(socket_path=path, secret=SECRET,
                              manager=self.manager,
                              allowed_uids={os.getuid() + 4242})
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)

        body = protocol.encode({})
        sent = protocol.sign(secret=SECRET, method="GET", path="/health",
                             body=body)
        conn = UnixHTTPConnection(path, timeout=10)
        # The server closes the connection at accept time, so the failure is a
        # transport error rather than an HTTP status — there is no response to
        # give a status to, which is the point.
        with self.assertRaises((ConnectionError, OSError)):
            conn.request("GET", "/health", body=body, headers=sent)
            conn.getresponse()
        conn.close()

    def test_the_allowlist_defaults_to_root_only(self):
        self.assertEqual(service.DEFAULT_PEER_UIDS, frozenset({0}))


class AdmissionControlTests(ServiceTestCase):
    """Drain, stop and resume — the switches an operator reaches for.

    The distinction under test is the one that matters at 3am: DRAIN empties a
    machine without destroying anything, STOP throws running work away on
    purpose. A drain that killed jobs would be an outage nobody asked for; a
    stop that let them run would not be a stop.
    """

    def test_a_fresh_host_takes_work(self):
        """No file means open. An executor on a new host must not need
        somebody to switch it on."""
        status, body = self.call("GET", "/control")
        self.assertEqual(status, 200)
        self.assertEqual(body["admission"]["state"], "open")

    def test_draining_refuses_a_mount_but_kills_nothing(self):
        self.call("POST", f"/capsules/{self.capsule}/mount",
                  {"limits": {"cpu_millicores": 1000, "ram_mb": 512,
                              "scratch_mb": 256, "pids": 64}})
        before = len(self.docker.containers)

        status, _ = self.call("POST", "/control/drain", {"reason": "reboot"})
        self.assertEqual(status, 200)

        status, body = self.call("POST", f"/capsules/{uuid.uuid4()}/mount",
                                 {"limits": {"cpu_millicores": 1000,
                                             "ram_mb": 512, "scratch_mb": 256,
                                             "pids": 64}})
        self.assertEqual(status, 503)
        self.assertIn("drained", body["error"])
        self.assertEqual(len(self.docker.containers), before,
                         "a drain must not destroy anything")

    def test_draining_refuses_new_JOBS_too(self):
        """Not only mounts. A Capsule mounted before the drain would otherwise
        keep starting jobs through the whole of it — which is exactly what an
        operator draining for a reboot is trying to stop."""
        self.call("POST", f"/capsules/{self.capsule}/mount",
                  {"limits": {"cpu_millicores": 1000, "ram_mb": 512,
                              "scratch_mb": 256, "pids": 64}})
        self.call("POST", "/control/drain")
        status, body = self.call("POST", "/jobs", {
            "capsule": self.capsule, "execution": str(uuid.uuid4()),
            "operation": "render_pdf", "params": {},
            "limits": {"cpu_millicores": 1000, "ram_mb": 512,
                       "scratch_mb": 256, "pids": 64}, "timeout": 60})
        self.assertEqual(status, 503)
        self.assertIn("drained", body["error"])

    def test_the_emergency_stop_destroys_every_runner(self):
        self.call("POST", f"/capsules/{self.capsule}/mount",
                  {"limits": {"cpu_millicores": 2000, "ram_mb": 2048,
                              "scratch_mb": 1024, "pids": 256}})
        self.call("POST", "/jobs", {
            "capsule": self.capsule, "execution": str(uuid.uuid4()),
            "operation": "render_pdf", "params": {},
            "limits": {"cpu_millicores": 1000, "ram_mb": 512,
                       "scratch_mb": 256, "pids": 64}, "timeout": 600})
        self.assertTrue(self.docker.containers, "nothing to destroy")

        status, body = self.call("POST", "/control/stop", {"reason": "incident"})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(body["runners_destroyed"], 1)
        self.assertEqual(self.docker.containers, {})

    def test_a_stopped_host_is_not_told_to_try_again(self):
        """Pressure is temporary and self-clearing; a stop is a person's
        decision. Telling somebody to retry into a stopped machine is how a
        refusal becomes a support ticket."""
        self.call("POST", "/control/stop", {"reason": "incident"})
        _, body = self.call("POST", f"/capsules/{uuid.uuid4()}/mount",
                            {"limits": {"cpu_millicores": 1000, "ram_mb": 512,
                                        "scratch_mb": 256, "pids": 64}})
        self.assertIn("stopped by an administrator", body["error"])
        self.assertNotIn("shortly", body["error"])

    def test_resume_is_the_only_way_back(self):
        for switch in ("drain", "stop"):
            with self.subTest(switch=switch):
                self.call("POST", f"/control/{switch}")
                _, body = self.call("GET", "/control")
                self.assertNotEqual(body["admission"]["state"], "open")
                self.call("POST", "/control/resume")
                _, body = self.call("GET", "/control")
                self.assertEqual(body["admission"]["state"], "open")

    def test_the_switch_survives_a_restart(self):
        """THE POINT OF PERSISTING IT. The unit restarts on failure five
        seconds later; a switch a crash can undo is not a switch."""
        self.call("POST", "/control/stop", {"reason": "incident"})

        # A brand-new manager and API over the same staging root, which is
        # what a restarted unit is.
        fresh = capsules.CapsuleManager(
            staging_root=self.root, slice_driver=CountingSliceDriver(),
            docker=FakeDocker(), generation="gen-restarted")
        state = control.read(fresh.staging_root)
        self.assertEqual(state["state"], "stopped")
        self.assertEqual(state["reason"], "incident")

    def test_health_says_whether_the_host_is_taking_work(self):
        self.call("POST", "/control/drain", {"reason": "maintenance"})
        _, body = self.call("GET", "/health")
        self.assertEqual(body["admission"]["state"], "draining")


class MetricsTests(ServiceTestCase):
    """Prometheus text, and what must never be a label in it."""

    def _scrape(self) -> str:
        body = protocol.encode({})
        sent = protocol.sign(secret=SECRET, method="GET", path="/metrics",
                             body=body)
        conn = UnixHTTPConnection(self.socket_path, timeout=10)
        try:
            conn.request("GET", "/metrics", body=body, headers=sent)
            response = conn.getresponse()
            raw = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("text/plain", response.getheader("Content-Type"))
            return raw
        finally:
            conn.close()

    def test_a_scrape_parses_as_prometheus_text(self):
        """Every non-comment line is `name value` or `name{labels} value`."""
        for line in self._scrape().splitlines():
            if not line or line.startswith("#"):
                continue
            with self.subTest(line=line):
                self.assertRegex(
                    line, r'^anastasia_executor_[a-z_]+(\{[^}]*\})? -?\d+(\.\d+)?$')

    def test_the_tier_is_reported_as_a_label(self):
        """So a dashboard can answer "how much of the fleet is on kata", and
        an alert can fire on one that silently fell back to containers."""
        self.assertIn('isolation_tier{tier="fake"} 1', self._scrape())

    def test_admission_is_a_number_an_alert_can_fire_on(self):
        """A host left draining after a maintenance window is invisible
        otherwise: nothing is broken, nothing errors, and no work runs."""
        self.assertIn("anastasia_executor_admitting 1", self._scrape())
        self.call("POST", "/control/drain")
        scrape = self._scrape()
        self.assertIn("anastasia_executor_admitting 0", scrape)
        self.assertIn('admission_state{state="draining"} 1', scrape)

    def test_no_user_job_or_capsule_identifier_is_ever_a_label(self):
        """THE ASSERTION THIS FILE EXISTS FOR.

        Prometheus keeps a distinct series per label combination forever, so a
        per-job label is both an unbounded cardinality explosion and a durable
        record of who ran what — in a monitoring system with none of the
        vault's access control.
        """
        self.call("POST", f"/capsules/{self.capsule}/mount",
                  {"limits": {"cpu_millicores": 2000, "ram_mb": 2048,
                              "scratch_mb": 1024, "pids": 256}})
        execution = str(uuid.uuid4())
        self.call("POST", "/jobs", {
            "capsule": self.capsule, "execution": execution,
            "operation": "render_pdf", "params": {},
            "limits": {"cpu_millicores": 1000, "ram_mb": 512,
                       "scratch_mb": 256, "pids": 64}, "timeout": 600})

        scrape = self._scrape()
        self.assertNotIn(execution, scrape)
        self.assertNotIn(self.capsule, scrape)
        for forbidden in ("user", "owner", "uuid", "job=", "execution="):
            with self.subTest(label=forbidden):
                self.assertNotIn(forbidden, scrape)

    def test_a_scrape_survives_an_unreachable_runtime(self):
        """A scrape must never be the thing that breaks — and an unreachable
        runtime is itself the most interesting thing to report."""
        def explode(*_args, **_kwargs):
            raise RuntimeError("the daemon is gone")

        self.docker.list_managed = explode
        self.docker.available = lambda: False
        scrape = self._scrape()
        self.assertIn("anastasia_executor_up 1", scrape)
        self.assertIn("anastasia_executor_runtime_reachable 0", scrape)

    def test_the_metrics_route_is_signed_like_every_other(self):
        """An unauthenticated route on this process would be the only one,
        which is exactly the sort of exception that outlives its reason."""
        conn = UnixHTTPConnection(self.socket_path, timeout=10)
        try:
            conn.request("GET", "/metrics",
                         headers={"Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 401)
        finally:
            conn.close()
