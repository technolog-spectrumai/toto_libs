"""The manager's HTTP surface, over a real socket.

A real server on a real loopback port rather than calling the handler
in-process: the things most worth testing here — that an unsigned request is
refused, that a body over the cap is rejected — live in the HTTP layer, and
mocking it would test the mock.
"""

from __future__ import annotations

import base64
import json
import shutil
import tempfile
import threading
import urllib.error
import urllib.request
import uuid

from django.test import SimpleTestCase

from toto.anastasia.limits import Limits
from toto.anastasia.manager import gears, protocol, service

from .fakes import CountingSliceDriver, FakeDocker

SECRET = "manager-test-secret"


class ServiceTestCase(SimpleTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="anastasia-svc-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.docker = FakeDocker()
        self.manager = gears.GearManager(
            staging_root=self.root, slice_driver=CountingSliceDriver(),
            docker=self.docker, generation="gen-http")
        self.httpd = service.serve(host="127.0.0.1", port=0, secret=SECRET,
                                   manager=self.manager)
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.shutdown)
        self.gear = str(uuid.uuid4())

    def call(self, method, path, payload=None, *, secret=SECRET, headers=None):
        body = protocol.encode(payload or {})
        sent = protocol.sign(secret=secret, method=method, path=path, body=body)
        sent["Content-Type"] = "application/json"
        sent.update(headers or {})
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, headers=sent,
            method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


class AuthenticationTests(ServiceTestCase):
    def test_a_signed_request_is_served(self):
        status, body = self.call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["generation"], "gen-http")

    def test_an_unsigned_request_is_refused(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/health", method="GET")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 401)

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
            request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/health", data=body,
                headers=sent, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status
            except urllib.error.HTTPError as exc:
                return exc.code

        self.assertEqual(fire(), 200)
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
        status, body = self.call("POST", "/executions", {
            "gear": self.gear, "execution": execution,
            "operation": "render_pdf",
            "limits": Limits(1000, 512, 256, 64).as_dict(),
        })
        self.assertEqual(status, 200)

        status, body = self.call("GET", f"/executions/{execution}",
                                 {"gear": self.gear})
        self.assertEqual(status, 200)
        self.assertTrue(body["running"])

        import os
        out = os.path.join(self.manager.exec_dir(self.gear, execution), "out")
        with open(os.path.join(out, "output.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4")
        status, body = self.call("GET", f"/executions/{execution}/out",
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
                status, body = self.call("POST", "/executions", {
                    "gear": self.gear, "execution": str(uuid.uuid4()),
                    "operation": "render_pdf", "params": hostile})
                self.assertEqual(status, 400)
                self.assertIn("does not take", body["error"])

    def test_an_unknown_operation_is_a_400_not_a_500(self):
        self.mount()
        status, body = self.call("POST", "/executions", {
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
        status, body = self.call("POST", "/executions", {
            "gear": self.gear, "execution": str(uuid.uuid4()),
            "operation": "render_pdf",
            "payload_b64": base64.b64encode(evil.getvalue()).decode()})
        self.assertEqual(status, 400)
        self.assertIn("escapes", body["error"])

    def test_bad_limits_are_a_400(self):
        self.mount()
        status, body = self.call("POST", "/executions", {
            "gear": self.gear, "execution": str(uuid.uuid4()),
            "operation": "render_pdf", "limits": {"gpus": 8}})
        self.assertEqual(status, 400)
        self.assertIn("gpus", body["error"])

    def test_a_missing_runner_image_is_a_409(self):
        self.mount()
        self.docker.images.discard("anastasia-pdf")
        status, body = self.call("POST", "/executions", {
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
        self.call("POST", "/executions", {
            "gear": self.gear, "execution": str(uuid.uuid4()),
            "operation": "render_pdf"})
        status, body = self.call("POST", "/reconcile", {"known_gears": []})
        self.assertEqual(status, 200)
        self.assertEqual(body["orphans_destroyed"], 1)


class RefusalTests(ServiceTestCase):
    def test_the_manager_will_not_start_without_a_secret(self):
        """An unauthenticated Docker manager is a remote shell."""
        with self.assertRaises(RuntimeError) as caught:
            service.serve(host="127.0.0.1", port=0, secret="",
                          manager=self.manager)
        self.assertIn("remote shell", str(caught.exception))
