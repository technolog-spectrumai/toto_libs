"""Moving one file between a Vault bucket and a Capsule: both directions, and
the door that must not be the cheap one.

Unit-level, over the fake backend's in-memory files area. What is NOT tested
here is the path rule — `safe_name`, the fd walk, the link refusal — because
that is `executor/files.py`'s and `test_files.py` proves it against a real
filesystem. What IS tested is everything above it: ownership, the two vault
directions, and above all that copying OUT of a capsule charges and scans
exactly as an upload does.

THE TEST THAT MATTERS is `MeteringParityTests`. `toto.vault` is pull-only, so
the upload door's sequence could not be extracted into one function both
sides call; it is reproduced in `transfer.to_bucket`. Two copies is how they
drift, and the parity test is what notices.
"""

from __future__ import annotations

import base64
import inspect
import json
import re
from unittest import mock

from django.core.files.base import ContentFile

from toto.anastasia import services, transfer
from toto.anastasia.limits import Limits
from toto.anastasia.tokens import CapsuleToken

from .base import AnastasiaTestCase, FakeRuntimeBackend


class TransferTestCase(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        # Every out-of-capsule copy writes real vault bytes, and the deployed
        # MEDIA_ROOT is a root-owned bind mount. Each test gets its own
        # directory and takes it away afterwards — the same shape
        # toto.ambrosia's test base uses, for the same reason.
        import shutil
        import tempfile

        from django.test import override_settings

        media = tempfile.mkdtemp(prefix="anastasia-transfer-")
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)

        from toto.vault.models import Bucket

        _row, self.raw = CapsuleToken.issue(owner=self.user, label="laptop")
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=Limits(3000, 6144, 6144, 768))
        services.mount(lease=self.lease, actor=self.user)
        self.bucket = Bucket.objects.create(
            name="Papers", slug="papers", owner=self.user, storage_backend="local")
        FakeRuntimeBackend.reset()

    def call(self, method, path, *, body=None, raw=None):
        kwargs = {"HTTP_AUTHORIZATION": f"Bearer {raw or self.raw}"}
        if body is not None:
            kwargs["content_type"] = "application/json"
            return getattr(self.client, method)(path, json.dumps(body), **kwargs)
        return getattr(self.client, method)(path, **kwargs)

    def files_url(self, suffix=""):
        return f"/api/v1/capsules/{self.lease.uuid}/files{suffix}"

    def vault_file(self, title="thesis.txt", body=b"Seven years of work.\n",
                   owner=None):
        from toto.vault.models import VaultFile

        vf = VaultFile(owner=owner or self.user, title=title, bucket=self.bucket,
                       file_type="text", key=title.replace(".", "-"))
        vf.file.save(title, ContentFile(body), save=False)
        vf.save()
        return vf


class FilesAreaApiTests(TransferTestCase):
    """The four verbs, over the token API."""

    def test_an_empty_area_lists_as_empty_and_complete(self):
        body = self.call("get", self.files_url()).json()
        self.assertEqual(body["files"], [])
        self.assertTrue(body["complete"])

    def test_a_put_then_a_list_then_a_get_round_trips(self):
        put = self.call("post", self.files_url("/put"), body={
            "name": "notes.txt",
            "data_b64": base64.b64encode(b"hello\n").decode()})
        self.assertEqual(put.status_code, 201, put.content)

        listed = self.call("get", self.files_url()).json()
        self.assertEqual([f["name"] for f in listed["files"]], ["notes.txt"])

        got = self.call("post", self.files_url("/get"),
                        body={"name": "notes.txt"}).json()
        self.assertEqual(base64.b64decode(got["data_b64"]), b"hello\n")
        self.assertEqual(got["bytes"], 6)

    def test_utf8_and_binary_survive(self):
        for name, payload in (("zażółć.txt", "gęślą jaźń 🐍\n".encode()),
                              ("logo.png", bytes(range(256)))):
            with self.subTest(name=name):
                self.call("post", self.files_url("/put"), body={
                    "name": name, "data_b64": base64.b64encode(payload).decode()})
                got = self.call("post", self.files_url("/get"),
                                body={"name": name}).json()
                self.assertEqual(base64.b64decode(got["data_b64"]), payload)

    def test_delete_removes_it(self):
        self.call("post", self.files_url("/put"),
                  body={"name": "gone.txt", "data_b64": "eA=="})
        deleted = self.call("post", self.files_url("/delete"),
                            body={"name": "gone.txt"})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.call("get", self.files_url()).json()["files"], [])

    # -- refusals -------------------------------------------------------------

    def test_the_executors_refusal_reaches_the_client_as_a_sentence(self):
        """`FilesRefused` carries what the executor said — "escapes the files
        area", "already exists" — and a 409 with the sentence is how the client
        shows it. It must NOT be a 500, and must not be confused with the
        runtime being down."""
        FakeRuntimeBackend.refuse_files = {"../etc/passwd"}
        response = self.call("post", self.files_url("/get"),
                             body={"name": "../etc/passwd"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "file_refused")
        self.assertIn("refused", response.json()["error"])

    def test_writing_over_an_existing_file_needs_replace(self):
        self.call("post", self.files_url("/put"),
                  body={"name": "a.txt", "data_b64": "eA=="})
        again = self.call("post", self.files_url("/put"),
                          body={"name": "a.txt", "data_b64": "eQ=="})
        self.assertEqual(again.status_code, 409)
        forced = self.call("post", self.files_url("/put"),
                           body={"name": "a.txt", "data_b64": "eQ==",
                                 "replace": True})
        self.assertEqual(forced.status_code, 201)

    def test_bad_base64_is_a_400_not_a_500(self):
        response = self.call("post", self.files_url("/put"),
                             body={"name": "a.txt", "data_b64": "not base64!"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "bad_content")

    def test_an_oversized_put_is_413_on_the_web_tier(self):
        """The ceiling is HERE too, because this process decodes into memory
        before the executor ever sees it — the far side's budget does not
        protect the web tier."""
        from toto.anastasia import api

        with mock.patch.object(api, "MAX_TRANSFER_BYTES", 8):
            response = self.call("post", self.files_url("/put"), body={
                "name": "big.bin", "data_b64": base64.b64encode(b"x" * 64).decode()})
        self.assertEqual(response.status_code, 413)

    def test_a_missing_name_is_named(self):
        for suffix in ("/get", "/put", "/delete"):
            with self.subTest(suffix=suffix):
                response = self.call("post", self.files_url(suffix),
                                     body={"data_b64": "eA=="})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "no_name")

    def test_the_name_travels_in_the_body_never_the_path(self):
        """A file name is a path — slashes, dots, unicode — and a URL is the
        wrong place to carry one. Pinned by asserting there is no route that
        would take it."""
        from django.urls import NoReverseMatch, reverse

        with self.assertRaises(NoReverseMatch):
            reverse("anastasia_api:capsule_file_get",
                    kwargs={"uuid": self.lease.uuid, "name": "x"})

    # -- ownership ----------------------------------------------------------

    def test_somebody_elses_capsule_is_a_404(self):
        _row, raw = CapsuleToken.issue(owner=self.other, label="theirs")
        self.assertEqual(
            self.call("get", self.files_url(), raw=raw).status_code, 404)

    def test_every_verb_needs_a_token(self):
        self.assertEqual(self.client.get(self.files_url()).status_code, 401)
        for suffix in ("/get", "/put", "/delete", "/from-vault", "/to-vault"):
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    self.client.post(self.files_url(suffix)).status_code, 401)

    def test_a_runtime_with_no_files_area_says_so(self):
        with mock.patch("toto.anastasia.runtime.get_backend",
                        return_value=mock.Mock(spec=["mount"])):
            response = self.call("get", self.files_url())
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()["code"], "unsupported")

    def test_files_is_not_swallowed_by_the_action_catch_all(self):
        """`capsules/<uuid>/<str:action>` is a POST-only catch-all placed last.
        A GET to `/files` reaching it would come back 405 and read like a
        broken endpoint rather than a shadowed route — which is exactly what
        happened to `/storage` once."""
        self.assertEqual(self.call("get", self.files_url()).status_code, 200)


class IntoCapsuleTests(TransferTestCase):
    """Vault -> Capsule: a read the user already paid for, into an area they
    already reserved. Not metered, and the test says so."""

    def test_a_vault_file_lands_in_the_capsule_byte_for_byte(self):
        vf = self.vault_file(body=b"Seven years of work.\n")
        response = self.call("post", self.files_url("/from-vault"),
                             body={"key": vf.key})
        self.assertEqual(response.status_code, 201, response.content)
        area = FakeRuntimeBackend.files[str(self.lease.uuid)]
        self.assertEqual(area["thesis.txt"], b"Seven years of work.\n")

    def test_the_name_inside_the_capsule_can_differ(self):
        vf = self.vault_file()
        self.call("post", self.files_url("/from-vault"),
                  body={"key": vf.key, "name": "data/input.txt"})
        self.assertIn("data/input.txt",
                      FakeRuntimeBackend.files[str(self.lease.uuid)])

    def test_somebody_elses_vault_file_is_a_404_not_a_copy(self):
        """Owner-filtered in the QUERY, so a stranger's key is indistinguishable
        from one that does not exist — and nothing is copied."""
        theirs = self.vault_file(title="secret.txt", owner=self.other)
        response = self.call("post", self.files_url("/from-vault"),
                             body={"key": theirs.key})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(FakeRuntimeBackend.files.get(str(self.lease.uuid), {}), {})

    def test_it_is_not_metered(self):
        """Nothing durable is created. A charge here would bill somebody twice
        for one Capsule."""
        from toto.vault.models import VaultUsageEvent

        vf = self.vault_file()
        before = VaultUsageEvent.objects.count()
        self.call("post", self.files_url("/from-vault"), body={"key": vf.key})
        self.assertEqual(VaultUsageEvent.objects.count(), before)

    def test_the_executors_refusal_is_the_clients_answer(self):
        FakeRuntimeBackend.refuse_files = {"../x"}
        vf = self.vault_file()
        response = self.call("post", self.files_url("/from-vault"),
                             body={"key": vf.key, "name": "../x"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "file_refused")

    def test_a_missing_key_is_named(self):
        response = self.call("post", self.files_url("/from-vault"), body={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "no_key")


class OutOfCapsuleTests(TransferTestCase):
    """Capsule -> Vault: the third door onto durable storage."""

    def _plant(self, name="result.csv", body=b"a,b\n1,2\n"):
        FakeRuntimeBackend.files[str(self.lease.uuid)] = {name: body}

    def _copy_out(self, name="result.csv", **extra):
        return self.call("post", self.files_url("/to-vault"),
                         body={"name": name, "bucket": self.bucket.slug, **extra})

    def test_a_capsule_file_becomes_a_vault_file_byte_for_byte(self):
        from toto.vault.models import VaultFile

        self._plant(body=b"a,b\n1,2\n")
        response = self._copy_out()
        self.assertEqual(response.status_code, 201, response.content)
        vf = VaultFile.objects.get(key=response.json()["key"])
        self.assertEqual(vf.bucket, self.bucket)
        self.assertEqual(vf.file_size_bytes, 8)
        with vf.file.open("rb") as handle:
            self.assertEqual(handle.read(), b"a,b\n1,2\n")

    def test_the_title_defaults_to_the_leaf_name(self):
        self._plant(name="out/deep/result.csv")
        body = self._copy_out(name="out/deep/result.csv").json()
        self.assertEqual(body["title"], "result.csv")

    def test_IT_IS_METERED_LIKE_AN_UPLOAD(self):
        """THE POINT. `storage.request` and `storage.transfer_mb`, recorded and
        charged, with idempotency keys, after the row exists."""
        from toto.vault.models import VaultUsageEvent

        self._plant(body=b"x" * (2 * 1024 * 1024))
        before = VaultUsageEvent.objects.count()
        response = self._copy_out()
        self.assertEqual(response.status_code, 201, response.content)
        events = VaultUsageEvent.objects.order_by("pk")[before:]
        codes = sorted(e.metric_code for e in events)
        self.assertEqual(codes, ["storage.request", "storage.transfer_mb"])
        mb = next(e for e in events if e.metric_code == "storage.transfer_mb")
        self.assertEqual(float(mb.quantity), 2.0)

    def test_a_refused_type_never_becomes_a_row(self):
        from toto.vault.models import VaultFile

        self._plant(name="tool.exe", body=b"MZ")
        with mock.patch("toto.vault.models.refused_file_types",
                        return_value={VaultFile.detect_type("", "tool.exe")}):
            response = self._copy_out(name="tool.exe")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "refused_type")
        self.assertFalse(VaultFile.objects.filter(owner=self.user).exists())

    def test_THE_SCAN_RUNS_ON_BYTES_A_RUNNER_WROTE(self):
        """The one door where the antivirus pass is not a formality: these
        bytes came out of a runner that may have been compromised by the very
        document it was asked to process. Refused BEFORE any row exists."""
        from toto.vault import scanning
        from toto.vault.models import VaultFile

        self._plant(name="payload.html", body=b"<script>evil()</script>")
        verdict = scanning.Verdict.refused("active-content", "scripts inside")
        with mock.patch.object(scanning, "should_scan", return_value=True), \
             mock.patch.object(scanning, "scan", return_value=verdict) as scan:
            response = self._copy_out(name="payload.html")
        self.assertTrue(scan.called, "the bytes were never scanned")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "infected")
        self.assertFalse(VaultFile.objects.filter(owner=self.user).exists())

    def test_a_clean_scan_is_recorded_against_the_file(self):
        from toto.vault import scanning

        self._plant(name="fine.txt")
        with mock.patch.object(scanning, "should_scan", return_value=True), \
             mock.patch.object(scanning, "scan",
                               return_value=scanning.Verdict.clean()), \
             mock.patch.object(scanning, "record") as record:
            response = self._copy_out(name="fine.txt")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(record.called)
        self.assertEqual(record.call_args.kwargs["door"], "capsule")

    def test_a_quota_refusal_creates_nothing_and_charges_nothing(self):
        from toto.quota import QuotaExceeded
        from toto.vault.models import VaultFile, VaultUsageEvent

        self._plant()
        before = VaultUsageEvent.objects.count()
        # `QuotaExceeded` takes the POLICY, whose `__str__` reads its period —
        # a bare string there is an AttributeError from inside the refusal.
        policy = mock.Mock()
        policy.period = "day"
        policy.metric_code = "storage.request"
        with mock.patch("toto.quota.check_quota",
                        side_effect=QuotaExceeded(policy, 1, 1)):
            response = self._copy_out()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(VaultFile.objects.filter(owner=self.user).exists())
        self.assertEqual(VaultUsageEvent.objects.count(), before)

    def test_a_title_a_download_could_not_serve_is_refused_before_any_row(self):
        """A newline in `VaultFile.title` makes `FileResponse` raise on every
        download of that file — a row nobody can fetch or, through the vault
        UI, delete. And a bidi override renders `evil\u202etxt.exe` as
        `evilexe.txt`. Refused with a sentence; nothing created, nothing
        charged, so there is nothing to clean up."""
        from toto.vault.models import VaultFile, VaultUsageEvent

        self._plant()
        before = VaultUsageEvent.objects.count()
        for title in ("two\nlines", "bidi\u202etxt.exe", "tab\tbed",
                      "x" * 256):
            with self.subTest(title=title[:12]):
                response = self._copy_out(title=title)
                self.assertEqual(response.status_code, 409, response.content)
                body = response.json()
                self.assertEqual(body["code"], "bad_title")
                self.assertIn("title", body["error"])
        self.assertFalse(VaultFile.objects.filter(owner=self.user).exists())
        self.assertEqual(VaultUsageEvent.objects.count(), before)
        # And a title of exactly the column width is fine.
        response = self._copy_out(title="y" * 255)
        self.assertEqual(response.status_code, 201, response.content)

    def test_the_stored_title_is_what_was_typed_not_a_rewrite(self):
        """Refuse, never rewrite: a person must never find a title in their
        bucket they did not type."""
        self._plant()
        body = self._copy_out(title="  Q3 results (final) — v2  ").json()
        self.assertEqual(body["title"], "Q3 results (final) — v2")

    def test_keys_are_unique_per_owner_not_per_bucket(self):
        """The vault's detail/download/delete endpoints look up by key alone,
        so a cross-bucket collision makes them ambiguous."""
        self._plant(name="report.txt")
        first = self._copy_out(name="report.txt").json()["key"]
        second = self._copy_out(name="report.txt").json()["key"]
        self.assertNotEqual(first, second)
        self.assertTrue(second.startswith(first))

    def test_somebody_elses_bucket_is_a_404(self):
        from toto.vault.models import Bucket

        theirs = Bucket.objects.create(name="Theirs", slug="theirs",
                                       owner=self.other, storage_backend="local")
        self._plant()
        response = self.call("post", self.files_url("/to-vault"),
                             body={"name": "result.csv", "bucket": theirs.slug})
        self.assertEqual(response.status_code, 404)

    def test_a_file_the_capsule_does_not_have_is_a_409_with_the_sentence(self):
        response = self._copy_out(name="ghost.txt")
        self.assertEqual(response.status_code, 409)
        self.assertIn("ghost.txt", response.json()["error"])


class MeteringParityTests(AnastasiaTestCase):
    """`transfer.to_bucket` reproduces the vault upload door's sequence
    because the door lives in pull-only `toto-base` and cannot be extracted.
    Two copies is how they drift. This reads BOTH and compares.

    Asserted on the SOURCE rather than by running both, because what has to
    stay equal is the set of metrics, the policy class and the tariff key —
    the things a refactor of either side would quietly rename.
    """

    @staticmethod
    def _source(obj) -> str:
        return inspect.getsource(obj)

    def _vault_door(self) -> str:
        from toto.vault.api_views import FileUploadApiView

        return self._source(FileUploadApiView.post)

    def _third_door(self) -> str:
        return self._source(transfer.to_bucket)

    def test_both_doors_meter_the_same_metrics(self):
        vault = set(re.findall(r'"(storage\.[a-z_]+)"', self._vault_door()))
        ours = {transfer.STORAGE_REQUEST, transfer.STORAGE_TRANSFER_MB}
        self.assertEqual(vault, ours,
                         "the vault upload door meters different metrics from "
                         "transfer.to_bucket — one of them is the cheap door")

    def test_both_doors_use_the_same_policy_and_tariff(self):
        vault = self._vault_door()
        ours = self._third_door()
        self.assertIn("VaultQuotaPolicy", vault)
        self.assertIn("VaultQuotaPolicy", ours)
        self.assertIn('price_for(request.user, "vault")', vault)
        self.assertEqual(transfer.TARIFF_KEY, "vault")
        self.assertIn("price_for(actor, TARIFF_KEY)", ours)

    def test_both_doors_scan_before_creating_and_charge_after(self):
        """The ORDER is the guarantee: a refusal that costs nothing has nothing
        to refund, and a scan after the row exists leaves a half-made row."""
        for label, src in (("vault", self._vault_door()), ("transfer", self._third_door())):
            with self.subTest(door=label):
                scan = src.index("scanning.scan(")
                create = src.index("VaultFile(")
                charge = src.index("charge(")
                quota = src.index("check_quota(")
                self.assertLess(quota, scan, f"{label}: scans before quota")
                self.assertLess(scan, create, f"{label}: creates before scanning")
                self.assertLess(create, charge, f"{label}: charges before the row exists")

    def test_the_third_door_uses_its_own_idempotency_keys(self):
        """Distinct from the upload door's, so a file that came in one way and
        is later re-uploaded another is two events, not one deduplicated."""
        ours = self._third_door()
        self.assertIn("anastasia.transfer.request:", ours)
        self.assertIn("anastasia.transfer.transfer:", ours)
        self.assertNotIn("vault.api_upload", ours)
