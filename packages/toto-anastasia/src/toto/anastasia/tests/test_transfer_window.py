"""The two-way transfer window: the Vault pane's rows, and one file per request.

Three layers. `panes.vault_pane_rows` is pinned as a shape, because the window
walks it. The two doors (`desk_transfer.copy_in`, `copy_out`) are pinned as
JSON a page can show a progress bar from — including the `stop` flag that says
whether every later file would be refused the same way. And the Files tab is
pinned to carry the window instead of the two one-way modals it replaced.

What each copy COSTS is `transfer.py`'s, proven in `test_transfer.py`; here it
is asserted only that each door goes through it.
"""

from __future__ import annotations

import shutil
import tempfile
from unittest import mock

from django.core.files.base import ContentFile
from django.test import Client, override_settings
from django.urls import reverse

from toto.anastasia import desk_transfer, panes, services, transfer

from .base import SMALL, FakeRuntimeBackend
from .test_views import DeskTestCase


class TransferWindowCase(DeskTestCase):
    def setUp(self):
        super().setUp()
        # Copying OUT writes real vault bytes, and the deployed MEDIA_ROOT is a
        # root-owned bind mount. Each test gets its own directory.
        media = tempfile.mkdtemp(prefix="anastasia-transfer-window-")
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)

        from toto.vault.models import Bucket

        self.lease = services.reserve(owner=self.user, name="lab", limits=SMALL)
        services.mount(lease=self.lease, actor=self.user)
        self.bucket = Bucket.objects.create(name="Papers", slug="papers",
                                            owner=self.user, storage_backend="local")

    # -- fixtures ------------------------------------------------------------------

    def _bucket(self, name, *, owner=None, backend="local"):
        from toto.vault.models import Bucket

        return Bucket.objects.create(name=name, slug=name.lower(),
                                     owner=owner or self.user, storage_backend=backend)

    def _dir(self, name, *, bucket=None, owner=None, parent=None):
        from toto.vault.models import VaultDirectory

        return VaultDirectory.objects.create(name=name, bucket=bucket or self.bucket,
                                             owner=owner or self.user, parent=parent)

    def _file(self, title="thesis.txt", body=b"seven years", *, owner=None,
              bucket=None, directory=None, **extra):
        from toto.vault.models import VaultFile

        vf = VaultFile(owner=owner or self.user, title=title,
                       bucket=self.bucket if bucket is None else bucket,
                       directory=directory, file_type="text",
                       key=title.replace(".", "-"), file_size_bytes=len(body), **extra)
        vf.file.save(title, ContentFile(body), save=False)
        vf.save()
        return vf

    def _area(self, **files):
        FakeRuntimeBackend.files[str(self.lease.uuid)] = dict(files)

    def _held(self):
        return FakeRuntimeBackend.files.get(str(self.lease.uuid), {})

    def _url(self, name, lease=None):
        return reverse(f"anastasia:{name}", args=[(lease or self.lease).uuid])

    def _post(self, name, data, lease=None, client=None):
        return (client or self.client).post(self._url(name, lease), data,
                                            HTTP_ACCEPT="application/json")


# --------------------------------------------------------------------------- #
# The Vault pane                                                               #
# --------------------------------------------------------------------------- #

class VaultPaneRowsTests(TransferWindowCase):
    def _rows(self, **kwargs):
        return panes.vault_pane_rows(self.user, **kwargs)["items"]

    @staticmethod
    def _label(row):
        return (row["t"], row.get("name") or row.get("title"), row["depth"])

    def test_buckets_then_folders_then_files_depth_first(self):
        """Each folder, then its subfolders, then its own files; a bucket's
        root files last — the Vault browser's order, so the window reads the
        same way the Vault does."""
        reports = self._dir("reports")
        q3 = self._dir("q3", parent=reports)
        self._file("deep.csv", directory=q3)
        self._file("summary.txt", directory=reports)
        self._file("root.txt")
        rows = self._rows()
        self.assertEqual([self._label(r) for r in rows], [
            ("bucket", "Papers", 0), ("dir", "reports", 1), ("dir", "q3", 2),
            ("file", "deep.csv", 3), ("file", "summary.txt", 2), ("file", "root.txt", 1),
        ])
        by = {(r["t"], r.get("name") or r.get("title")): r for r in rows}
        self.assertEqual(by[("dir", "q3")]["path"], "reports/q3")
        self.assertEqual(by[("dir", "reports")]["pid"], by[("bucket", "Papers")]["id"])
        self.assertEqual(by[("dir", "q3")]["pid"], by[("dir", "reports")]["id"])
        self.assertEqual(by[("file", "deep.csv")]["pid"], by[("dir", "q3")]["id"])
        self.assertEqual(by[("dir", "q3")]["dpk"], q3.pk)
        self.assertTrue(by[("bucket", "Papers")]["writable"])
        self.assertTrue(by[("dir", "q3")]["writable"])
        self.assertEqual(by[("file", "deep.csv")]["size"], len(b"seven years"))

    def test_an_empty_folder_i_own_is_listed_as_a_destination(self):
        self._dir("empty")
        rows = self._rows()
        folder = next(r for r in rows if r["t"] == "dir")
        self.assertEqual((folder["name"], folder["n_files"], folder["writable"]),
                         ("empty", 0, True))

    def test_only_my_own_files_are_listed(self):
        """The copy-in door accepts only the person's own files; a pane that
        offered another's would offer a refusal."""
        self._file("mine.txt")
        self._file("theirs.txt", owner=self.other)
        titles = [r["title"] for r in self._rows() if r["t"] == "file"]
        self.assertEqual(titles, ["mine.txt"])

    def test_a_file_in_somebody_elses_folder_hangs_from_that_folder(self):
        """A file whose folder the person does not own must still appear under
        it — not vanish — and that folder is not a destination."""
        shared = self._dir("shared", owner=self.other)
        self._file("mine.txt", directory=shared)
        rows = self._rows()
        folder = next(r for r in rows if r["t"] == "dir")
        mine = next(r for r in rows if r["t"] == "file")
        self.assertEqual(folder["name"], "shared")
        self.assertFalse(folder["writable"])
        self.assertEqual(mine["pid"], folder["id"])

    def test_a_bucket_holding_my_file_that_is_not_mine_is_a_source_only(self):
        theirs = self._bucket("Theirs", owner=self.other)
        self._file("lent.txt", bucket=theirs)
        bucket = next(r for r in self._rows() if r.get("slug") == "theirs")
        self.assertFalse(bucket["writable"])
        self.assertTrue(any(r["t"] == "file" and r["title"] == "lent.txt"
                            for r in self._rows()))

    def test_an_encrypted_file_says_why_it_cannot_be_copied(self):
        """It would reach the Capsule as ciphertext."""
        self._file("secret.pdf", is_encrypted=True)
        row = next(r for r in self._rows() if r["t"] == "file")
        self.assertEqual((row["copyable"], row["why"]), (False, panes.ENCRYPTED))

    def test_a_remote_bucket_is_neither_a_source_of_bytes_nor_a_destination(self):
        remote = self._bucket("Cloud", backend="s3")
        self._file("far.txt", bucket=remote)
        rows = self._rows()
        bucket = next(r for r in rows if r.get("slug") == "cloud")
        far = next(r for r in rows if r["t"] == "file" and r["title"] == "far.txt")
        self.assertEqual((bucket["local"], bucket["writable"]), (False, False))
        self.assertEqual((far["copyable"], far["why"]), (False, panes.REMOTE))

    def test_ids_are_unique_and_every_parent_is_a_row(self):
        """One counter: a folder and a file never share an id."""
        top = self._dir("top")
        self._file("a.txt", directory=top)
        self._file("b.txt")
        rows = self._rows()
        ids = [r["id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))
        for row in rows:
            if row["pid"] is not None:
                self.assertIn(row["pid"], ids)

    def test_a_truncated_pane_says_so(self):
        self._file("a.txt")
        self._file("b.txt")
        result = panes.vault_pane_rows(self.user, limit=1)
        self.assertTrue(result["truncated"])
        self.assertEqual(len([r for r in result["items"] if r["t"] == "file"]), 1)


class ResolveOwnedDirectoryTests(TransferWindowCase):
    def test_a_folder_i_own_in_that_bucket(self):
        folder = self._dir("reports")
        self.assertEqual(panes.resolve_owned_directory(self.user, str(folder.pk),
                                                       self.bucket), folder)

    def test_every_other_folder_is_the_same_absence(self):
        """In another bucket, owned by somebody else, or not a number: never
        "that folder exists but is not yours"."""
        elsewhere = self._dir("elsewhere", bucket=self._bucket("Other"))
        theirs = self._dir("theirs", owner=self.other)
        for raw in (elsewhere.pk, theirs.pk, "abc", "-1", "0", "", None, "999999",
                    "²", "99999999999999999999"):
            with self.subTest(raw=raw):
                self.assertIsNone(panes.resolve_owned_directory(self.user, raw,
                                                                self.bucket))


# --------------------------------------------------------------------------- #
# Into the Capsule                                                             #
# --------------------------------------------------------------------------- #

class CopyInDoorTests(TransferWindowCase):
    def test_one_file_lands_in_the_chosen_folder_and_the_answer_is_json(self):
        vf = self._file()
        response = self._post("files_copy_in", {"file": vf.pk, "folder": "/in/data/"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["from"]["id"], vf.pk)
        self.assertEqual(self._held(), {"in/data/thesis.txt": b"seven years"})

    def test_without_a_folder_it_lands_at_the_top_of_the_area(self):
        vf = self._file()
        self._post("files_copy_in", {"file": vf.pk})
        self.assertEqual(list(self._held()), ["thesis.txt"])

    def test_copying_in_is_not_metered(self):
        from toto.vault.models import VaultUsageEvent

        vf = self._file()
        before = VaultUsageEvent.objects.count()
        response = self._post("files_copy_in", {"file": vf.pk})
        # Only a copy that HAPPENED proves anything about what it costs.
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIn("thesis.txt", self._held())
        self.assertEqual(VaultUsageEvent.objects.count(), before)

    def test_replace_overwrites_a_file_of_the_same_name(self):
        vf = self._file()
        self._area(**{"thesis.txt": b"old"})
        refused = self._post("files_copy_in", {"file": vf.pk})
        self.assertEqual(self._held(), {"thesis.txt": b"old"})
        self.assertFalse(refused.json()["stop"])
        response = self._post("files_copy_in", {"file": vf.pk, "replace": "1"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self._held(), {"thesis.txt": b"seven years"})

    def test_somebody_elses_file_is_a_404_that_names_nothing(self):
        theirs = self._file("secret.txt", owner=self.other)
        for raw in (theirs.pk, "abc", "", 999999):
            with self.subTest(raw=raw):
                response = self._post("files_copy_in", {"file": raw})
                self.assertEqual(response.status_code, 404)
                body = response.json()
                self.assertEqual((body["code"], body["stop"]), ("no_such_file", False))
                self.assertNotIn("secret", response.content.decode())
        self.assertEqual(self._held(), {})

    def test_an_id_int_cannot_read_is_the_same_404_not_a_500(self):
        """`str.isdigit` is true for "²", which `int` refuses; past 2**63-1
        SQLite overflows instead of finding nothing."""
        for raw in ("²", "99999999999999999999", "1e3", " "):
            with self.subTest(raw=raw):
                response = self._post("files_copy_in", {"file": raw})
                self.assertEqual((response.status_code, response.json()["code"]),
                                 (404, "no_such_file"))

    def test_an_encrypted_file_is_refused_before_a_byte_is_read(self):
        vf = self._file("sealed.pdf", is_encrypted=True)
        with mock.patch.object(transfer, "to_capsule") as copy:
            response = self._post("files_copy_in", {"file": vf.pk})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "encrypted")
        copy.assert_not_called()

    def test_a_file_in_a_remote_bucket_is_refused(self):
        vf = self._file("far.txt", bucket=self._bucket("Cloud", backend="s3"))
        with mock.patch.object(transfer, "to_capsule") as copy:
            response = self._post("files_copy_in", {"file": vf.pk})
        self.assertEqual((response.status_code, response.json()["code"]),
                         (409, "not_local"))
        copy.assert_not_called()

    def test_a_file_over_the_limit_is_refused_before_it_is_read(self):
        """This process would hold the whole file; the limit exists so it
        does not."""
        vf = self._file("big.bin", b"x" * 11)
        with mock.patch.object(desk_transfer, "MAX_UPLOAD_BYTES", 3), \
                mock.patch.object(transfer, "to_capsule") as copy:
            response = self._post("files_copy_in", {"file": vf.pk})
        self.assertEqual((response.status_code, response.json()["code"]),
                         (413, "too_large"))
        self.assertFalse(response.json()["stop"])
        copy.assert_not_called()

    def test_the_executors_refusal_is_about_that_file_and_stops_nothing(self):
        vf = self._file()
        FakeRuntimeBackend.refuse_files = {"thesis.txt"}
        body = self._post("files_copy_in", {"file": vf.pk}).json()
        self.assertEqual((body["code"], body["stop"]), ("file_refused", False))

    def test_a_runtime_that_does_not_answer_stops_the_batch(self):
        """Nobody's fault, and every later file would meet it too."""
        from toto.anastasia.runtime import RuntimeUnavailable

        class Down(FakeRuntimeBackend):
            def capsule_file_write(self, lease, name, data, replace=False):
                raise RuntimeUnavailable("the fake manager is down")

        vf = self._file()
        with mock.patch("toto.anastasia.runtime.get_backend", return_value=Down()):
            response = self._post("files_copy_in", {"file": vf.pk})
        self.assertEqual(response.status_code, 503)
        self.assertEqual((response.json()["code"], response.json()["stop"]),
                         ("runtime_unavailable", True))

    def test_csrf_is_enforced(self):
        """A session door that writes must refuse a POST without the token —
        the bearer API's doors are exempt because a token is not a cookie."""
        vf = self._file()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        response = self._post("files_copy_in", {"file": vf.pk}, client=strict)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._held(), {})

    def test_it_is_post_only_owner_only_and_gone_with_the_reservation(self):
        vf = self._file()
        self.assertEqual(self.client.get(self._url("files_copy_in")).status_code, 405)
        theirs = services.reserve(owner=self.other, name="theirs", limits=SMALL)
        self.assertEqual(self._post("files_copy_in", {"file": vf.pk},
                                    lease=theirs).status_code, 404)
        services.release(lease=self.lease)
        self.assertEqual(self._post("files_copy_in", {"file": vf.pk}).status_code, 404)


# --------------------------------------------------------------------------- #
# Out of the Capsule                                                           #
# --------------------------------------------------------------------------- #

class CopyOutDoorTests(TransferWindowCase):
    def test_one_file_lands_in_the_chosen_vault_folder(self):
        from toto.vault.models import VaultFile

        folder = self._dir("reports")
        self._area(**{"out/result.csv": b"a,b\n"})
        response = self._post("files_copy_out", {
            "name": "out/result.csv", "bucket": "papers", "directory": folder.pk})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["directory"], {"id": folder.pk, "name": "reports"})
        made = VaultFile.objects.get(pk=body["id"])
        self.assertEqual((made.title, made.bucket_id, made.directory_id),
                         ("result.csv", self.bucket.pk, folder.pk))

    def test_without_a_folder_it_lands_at_the_bucket_root(self):
        from toto.vault.models import VaultFile

        self._area(**{"result.csv": b"a,b\n"})
        body = self._post("files_copy_out", {"name": "result.csv",
                                             "bucket": "papers"}).json()
        self.assertIsNone(body["directory"])
        self.assertIsNone(VaultFile.objects.get(pk=body["id"]).directory_id)

    def test_copying_out_is_metered_like_an_upload(self):
        from toto.vault.models import VaultUsageEvent

        self._area(**{"result.csv": b"a,b\n"})
        before = VaultUsageEvent.objects.count()
        self._post("files_copy_out", {"name": "result.csv", "bucket": "papers"})
        self.assertGreater(VaultUsageEvent.objects.count(), before)

    def test_a_folder_that_is_not_mine_or_not_in_that_bucket_is_a_404(self):
        """And it STOPS: the destination is the same for every file."""
        from toto.vault.models import VaultFile

        elsewhere = self._dir("elsewhere", bucket=self._bucket("Other"))
        theirs = self._dir("theirs", owner=self.other)
        self._area(**{"result.csv": b"a,b\n"})
        for folder in (elsewhere, theirs):
            with self.subTest(folder=folder.name):
                response = self._post("files_copy_out", {
                    "name": "result.csv", "bucket": "papers", "directory": folder.pk})
                self.assertEqual(response.status_code, 404)
                self.assertEqual((response.json()["code"], response.json()["stop"]),
                                 ("no_such_folder", True))
        self.assertFalse(VaultFile.objects.exists())

    def test_somebody_elses_bucket_is_a_404_that_stops(self):
        self._bucket("Theirs", owner=self.other)
        self._area(**{"result.csv": b"a,b\n"})
        response = self._post("files_copy_out", {"name": "result.csv", "bucket": "theirs"})
        self.assertEqual((response.status_code, response.json()["stop"]), (404, True))

    def test_a_remote_bucket_is_refused_and_stops(self):
        """`to_bucket` writes to this server's disk; a remote bucket's row
        pointing at local bytes would be a lie."""
        from toto.vault.models import VaultFile

        self._bucket("Cloud", backend="s3")
        self._area(**{"result.csv": b"a,b\n"})
        response = self._post("files_copy_out", {"name": "result.csv", "bucket": "cloud"})
        self.assertEqual((response.status_code, response.json()["code"],
                          response.json()["stop"]), (409, "remote_bucket", True))
        self.assertFalse(VaultFile.objects.exists())

    def test_a_quota_refusal_stops_the_batch(self):
        """About the person, not the file: every later file would meet it."""
        from toto.quota import QuotaExceeded

        policy = mock.Mock()
        policy.period = "day"
        policy.metric_code = "storage.request"
        self._area(**{"result.csv": b"a,b\n"})
        with mock.patch("toto.quota.check_quota",
                        side_effect=QuotaExceeded(policy, 1, 1)):
            response = self._post("files_copy_out", {"name": "result.csv",
                                                     "bucket": "papers"})
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["stop"])

    def test_a_refused_type_is_about_that_file(self):
        self._area(**{"result.csv": b"a,b\n"})
        with mock.patch("toto.vault.models.refused_file_types", return_value={"csv"}):
            body = self._post("files_copy_out", {"name": "result.csv",
                                                 "bucket": "papers"}).json()
        self.assertEqual((body["code"], body["stop"]), ("refused_type", False))

    def test_a_key_taken_in_the_bucket_by_somebody_else_gets_a_suffix(self):
        """The database allows one key per BUCKET, and a bucket holds other
        people's files — a gateway upload is one. A key free for the owner was
        an IntegrityError: a 500, and bytes on disk that no row pointed at."""
        from toto.vault.models import VaultFile

        self._file("results", owner=self.other)
        self._area(**{"out/results": b"r"})
        response = self._post("files_copy_out", {"name": "out/results",
                                                 "bucket": "papers"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(VaultFile.objects.get(pk=response.json()["id"]).key,
                         "results-1")

    def test_a_lost_race_for_the_key_is_about_that_file(self):
        self._area(**{"result.csv": b"a,b\n"})
        with mock.patch.object(transfer, "to_bucket", side_effect=transfer.TransferRefused(
                "taken", code="name_taken")):
            body = self._post("files_copy_out", {"name": "result.csv",
                                                 "bucket": "papers"}).json()
        self.assertEqual((body["code"], body["stop"]), ("name_taken", False))

    def test_a_nameless_copy_is_a_400(self):
        response = self._post("files_copy_out", {"bucket": "papers"})
        self.assertEqual((response.status_code, response.json()["code"]), (400, "no_name"))

    def test_it_is_post_only(self):
        self.assertEqual(self.client.get(self._url("files_copy_out")).status_code, 405)


# --------------------------------------------------------------------------- #
# Refreshing the panes                                                         #
# --------------------------------------------------------------------------- #

class TransferRowsTests(TransferWindowCase):
    def test_both_panes_come_back_fresh(self):
        self._file("thesis.txt")
        self._area(**{"out/result.csv": b"a,b\n"})
        body = self.client.get(self._url("files_transfer_rows")).json()
        self.assertIn("thesis.txt", [r.get("title") for r in body["vault"]["items"]])
        self.assertEqual([r.get("path") for r in body["capsule"]["items"]],
                         ["out", "out/result.csv"])
        self.assertTrue(body["capsule"]["answered"])

    def test_an_unmounted_capsule_is_not_asked_and_says_so(self):
        """"Nobody answered" must not be drawn as an empty area."""
        services.unmount(lease=self.lease, actor=self.user)
        body = self.client.get(self._url("files_transfer_rows")).json()
        self.assertEqual((body["capsule"]["answered"], body["capsule"]["items"]),
                         (False, []))
        self.assertIn("items", body["vault"])

    def test_it_is_owner_only(self):
        theirs = services.reserve(owner=self.other, name="theirs", limits=SMALL)
        self.assertEqual(self.client.get(self._url("files_transfer_rows", theirs)).status_code,
                         404)


# --------------------------------------------------------------------------- #
# The folder guard in transfer.to_bucket                                       #
# --------------------------------------------------------------------------- #

class ToBucketFolderGuardTests(TransferWindowCase):
    """`to_bucket` took any `directory` and stored it. No door passed one until
    the transfer window; the guard lives in the function so the next door
    cannot forget it."""

    def test_a_folder_from_another_bucket_or_owner_is_refused_before_anything(self):
        from toto.vault.models import VaultFile, VaultUsageEvent

        self._area(**{"result.csv": b"a,b\n"})
        before = VaultUsageEvent.objects.count()
        for folder in (self._dir("elsewhere", bucket=self._bucket("Other")),
                       self._dir("theirs", owner=self.other)):
            with self.subTest(folder=folder.name):
                with self.assertRaises(transfer.TransferRefused) as caught:
                    transfer.to_bucket(lease=self.lease, name="result.csv",
                                       bucket=self.bucket, actor=self.user,
                                       directory=folder)
                self.assertEqual(caught.exception.refusal_code, "bad_folder")
        self.assertFalse(VaultFile.objects.exists())
        self.assertEqual(VaultUsageEvent.objects.count(), before)

    def test_a_lost_race_for_the_key_leaves_no_bytes_and_no_charge(self):
        """The bytes are written before the row. When the row loses the key to
        another copy, bytes that no row points at are disk nobody is billed for
        and nothing sweeps."""
        import os

        from django.conf import settings
        from django.db import IntegrityError

        from toto.vault.models import VaultFile, VaultUsageEvent

        self._area(**{"result.csv": b"a,b\n"})
        before = VaultUsageEvent.objects.count()
        with mock.patch.object(VaultFile, "save", side_effect=IntegrityError("taken")):
            with self.assertRaises(transfer.TransferRefused) as caught:
                transfer.to_bucket(lease=self.lease, name="result.csv",
                                   bucket=self.bucket, actor=self.user)
        self.assertEqual(caught.exception.refusal_code, "name_taken")
        left = [name for _root, _dirs, names in os.walk(settings.MEDIA_ROOT)
                for name in names]
        self.assertEqual(left, [])
        self.assertEqual(VaultUsageEvent.objects.count(), before)


# --------------------------------------------------------------------------- #
# The Files tab carries the window                                             #
# --------------------------------------------------------------------------- #

class FilesTabWindowTests(TransferWindowCase):
    def test_the_files_tab_carries_the_window_and_the_vault_pane(self):
        self._file()
        response = self.client.get(self._url("capsule_files"))
        self.assertContains(response, 'data-testid="transfer-window"')
        self.assertContains(response, 'id="transfer-vault-rows"')
        self.assertContains(response, self._url("files_copy_in"))
        self.assertContains(response, self._url("files_copy_out"))
        self.assertContains(response, self._url("files_transfer_rows"))
        self.assertContains(response, "open-transfer")
        # What each direction costs, said before anything is pressed.
        self.assertContains(response, "charged and virus-scanned")
        self.assertContains(response, "costs nothing")

    def test_the_one_way_modals_it_replaced_are_gone(self):
        self._file()
        body = self.client.get(self._url("capsule_files")).content.decode()
        self.assertNotIn('name="file"', body)
        self.assertNotIn("/files/from-vault/", body)
        self.assertNotIn("/files/to-vault/", body)

    def test_rows_are_keyed_by_what_they_are_not_where_they_are(self):
        """Keyed by position, a refresh made Alpine reuse one file's checkbox
        for another — and a charged copy out could take a file drawn unticked."""
        self._file()
        body = self.client.get(self._url("capsule_files")).content.decode()
        self.assertIn("'vf-' + row.fpk", body)
        self.assertIn("'c-' + row.t + ':' + row.path", body)
        self.assertNotIn(":key=\"'v-' + row.id\"", body)
        self.assertNotIn(":key=\"'c-' + row.id\"", body)

    def test_an_unmounted_capsule_has_no_window(self):
        services.unmount(lease=self.lease, actor=self.user)
        response = self.client.get(self._url("capsule_files"))
        self.assertNotContains(response, 'data-testid="transfer-window"')
