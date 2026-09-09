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
import threading
import urllib.error
import urllib.request
import uuid

from django.test import SimpleTestCase

from toto.anastasia.limits import Limits
from toto.anastasia.executor import gears, protocol, service
from toto.anastasia.executor_backend import UnixHTTPConnection

from .fakes import CountingSliceDriver, FakeDocker

SECRET = "executor-test-secret"


class ServiceTestCase(SimpleTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="anastasia-svc-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.docker = FakeDocker()
        self.manager = gears.GearManager(
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
        self.gear = str(uuid.uuid4())

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
        return self.call("POST", f"/gears/{self.gear}/mount",
                         {"limits": Limits(2000, 1024, 512, 256).as_dict()})

    def test_mount_then_execute_then_collect_then_unmount(self):
        status, body = self.mount()
        self.assertEqual(status, 200)
        self.assertEqual(body["manager_generation"], "gen-http")

        execution = str(uuid.uuid4())
        status, body = self.call("POST", "/jobs", {
            "gear": self.gear, "execution": execution,
            "operation": "render_pdf",
            "limits": Limits(1000, 512, 256, 64).as_dict(),
        })
        self.assertEqual(status, 200)

        status, body = self.call("GET", f"/jobs/{execution}",
                                 {"gear": self.gear})
        self.assertEqual(status, 200)
        self.assertTrue(body["running"])

        import os
        out = os.path.join(self.manager.exec_dir(self.gear, execution), "out")
        with open(os.path.join(out, "output.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4")
        status, body = self.call("GET", f"/jobs/{execution}/out",
                                 {"gear": self.gear})
        self.assertEqual(status, 200)
        self.assertTrue(base64.b64decode(body["tar_b64"]))

        status, body = self.call("POST", f"/gears/{self.gear}/unmount")
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
                    "gear": self.gear, "execution": str(uuid.uuid4()),
                    "operation": "render_pdf", "params": hostile})
                self.assertEqual(status, 400)
                self.assertIn("does not take", body["error"])

    def test_an_unknown_operation_is_a_400_not_a_500(self):
        self.mount()
        status, body = self.call("POST", "/jobs", {
            "gear": self.gear, "execution": str(uuid.uuid4()),
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
            "gear": self.gear, "execution": str(uuid.uuid4()),
            "operation": "render_pdf",
            "payload_b64": base64.b64encode(evil.getvalue()).decode()})
        self.assertEqual(status, 400)
        self.assertIn("escapes", body["error"])

    def test_bad_limits_are_a_400(self):
        self.mount()
        status, body = self.call("POST", "/jobs", {
            "gear": self.gear, "execution": str(uuid.uuid4()),
            "operation": "render_pdf", "limits": {"gpus": 8}})
        self.assertEqual(status, 400)
        self.assertIn("gpus", body["error"])

    def test_a_missing_runner_image_is_a_409(self):
        self.mount()
        self.docker.images.discard("anastasia-pdf")
        status, body = self.call("POST", "/jobs", {
            "gear": self.gear, "execution": str(uuid.uuid4()),
            "operation": "render_pdf"})
        self.assertEqual(status, 409)
        self.assertIn("runner image", body["error"])

    def test_pool_reports_the_ceiling_and_the_pressure(self):
        status, body = self.call("GET", "/pool")
        self.assertEqual(status, 200)
        self.assertIn("slice_driver", body)
        self.assertIn("admitting", body["pressure"])

    def test_reconcile_takes_the_callers_gear_list(self):
        self.mount()
        self.call("POST", "/jobs", {
            "gear": self.gear, "execution": str(uuid.uuid4()),
            "operation": "render_pdf"})
        status, body = self.call("POST", "/reconcile", {"known_gears": []})
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
