"""Version history for vault files: dedupe, append-only restore, pruning.

No git anywhere, deliberately — see the plan's evaluation. What is tested here
is the three properties that replace it: bodies stored once per digest, a
counter that only ever moves forward, and pruning that cannot eat a version
somebody named.
"""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from toto.vault import versions
from toto.vault.models import FileVersion, VaultFile, VersionBlob

User = get_user_model()


class VersionTestCase(TestCase):
    """A real VaultFile on disk — the service reads and writes actual bytes.

    Its own MEDIA_ROOT, the convention EncryptedEditLockTests uses: the real one
    belongs to the docker stack and is not writable by the test runner, so
    without this every test here dies on PermissionError rather than on anything
    it was meant to assert.
    """

    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp(prefix="vault-versions-")
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.temp_media, ignore_errors=True)

    def setUp(self):
        self._media = override_settings(MEDIA_ROOT=self.temp_media)
        self._media.enable()
        self.addCleanup(self._media.disable)
        self.author = User.objects.create_user(username="writer")
        self.other = User.objects.create_user(username="second-writer")
        self.file = self._make_file(b"<document>one</document>")

    def _make_file(self, body: bytes, title="Doc", key="doc"):
        vault_file = VaultFile(owner=self.author, title=title, key=key,
                               file_type="document")
        vault_file.file.save(f"{key}.xml", ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def _body(self):
        with self.file.file.open("rb") as handle:
            return handle.read()


class SaveVersionTests(VersionTestCase):
    def test_the_first_version_is_v1_and_numbering_climbs(self):
        first = versions.save_version(self.file, author=self.author)
        second = versions.save_version(self.file, author=self.author)

        self.assertEqual(first.number, 1)
        self.assertEqual(second.number, 2)

    def test_a_version_defaults_to_whatever_is_in_the_file(self):
        # The ordinary case: the editor autosaved, and the user is naming the
        # state that is already on disk.
        version = versions.save_version(self.file, author=self.author)
        self.assertEqual(version.read(), b"<document>one</document>")

    def test_authorship_is_recorded_and_survives_the_author(self):
        # A NON-OWNER author, deliberately: VaultFile.owner cascades, so
        # deleting the owner takes the whole file and its history with it. What
        # SET_NULL protects is the other case — cyprian lets a team edit a wiki
        # page nobody on it owns, and one of them leaving must not blank the
        # history.
        version = versions.save_version(self.file, author=self.other)
        self.assertEqual(version.author, self.other)

        self.other.delete()
        version.refresh_from_db()
        self.assertIsNone(version.author)      # the version itself remains

    def test_an_anonymous_saver_is_recorded_as_nobody_rather_than_crashing(self):
        from django.contrib.auth.models import AnonymousUser

        version = versions.save_version(self.file, author=AnonymousUser())
        self.assertIsNone(version.author)

    def test_two_versions_cannot_share_a_number(self):
        from django.db import IntegrityError, transaction

        versions.save_version(self.file, author=self.author)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FileVersion.objects.create(
                    file=self.file, number=1,
                    blob=VersionBlob.objects.first())


class DedupeTests(VersionTestCase):
    """The one place this design beats naive snapshots."""

    def test_identical_bodies_share_one_blob(self):
        first = versions.save_version(self.file, author=self.author)
        second = versions.save_version(self.file, author=self.author)

        self.assertEqual(first.blob_id, second.blob_id)
        self.assertEqual(VersionBlob.objects.count(), 1)

    def test_different_bodies_get_different_blobs(self):
        versions.save_version(self.file, author=self.author)
        with self.file.file.open("wb") as handle:
            handle.write(b"<document>two</document>")
        versions.save_version(self.file, author=self.author)

        self.assertEqual(VersionBlob.objects.count(), 2)

    def test_the_blob_is_addressed_by_its_digest(self):
        import hashlib

        version = versions.save_version(self.file, author=self.author)
        expected = hashlib.sha256(b"<document>one</document>").hexdigest()

        self.assertEqual(version.blob.content_hash, expected)
        self.assertIn(expected, version.blob.data.name)
        self.assertIn(f"/{expected[:2]}/", version.blob.data.name)

    def test_a_blob_cannot_be_edited(self):
        from django.core.exceptions import ValidationError

        blob = versions.save_version(self.file, author=self.author).blob
        blob.size_bytes = 999
        with self.assertRaises(ValidationError):
            blob.save()

    def test_restoring_then_saving_writes_no_new_bytes(self):
        # The headline claim: an unchanged 8 MB illustration is not stored twice.
        original = versions.save_version(self.file, author=self.author)
        with self.file.file.open("wb") as handle:
            handle.write(b"<document>changed</document>")
        versions.save_version(self.file, author=self.author)
        self.assertEqual(VersionBlob.objects.count(), 2)

        versions.restore_version(original, actor=self.author)

        self.assertEqual(VersionBlob.objects.count(), 2)   # nothing new written
        self.assertEqual(FileVersion.objects.filter(file=self.file).count(), 3)


class RestoreTests(VersionTestCase):
    def test_restoring_writes_the_old_body_back(self):
        first = versions.save_version(self.file, author=self.author)
        with self.file.file.open("wb") as handle:
            handle.write(b"<document>two</document>")

        versions.restore_version(first, actor=self.author)

        self.assertEqual(self._body(), b"<document>one</document>")

    def test_restoring_moves_the_counter_forward_and_never_back(self):
        # Append-only, the house doctrine: the ledger, the mint chain and
        # ChainedRecord all refuse to rewrite history, and so does this.
        first = versions.save_version(self.file, author=self.author)
        with self.file.file.open("wb") as handle:
            handle.write(b"<document>two</document>")
        versions.save_version(self.file, author=self.author)

        restored = versions.restore_version(first, actor=self.author)

        self.assertEqual(restored.number, 3)
        self.assertIn("restored from v1", str(restored.label))
        # Everything between is still there.
        self.assertEqual(
            list(FileVersion.objects.filter(file=self.file)
                 .order_by("number").values_list("number", flat=True)),
            [1, 2, 3])

    def test_restoring_refreshes_the_files_own_hash_and_size(self):
        import hashlib

        first = versions.save_version(self.file, author=self.author)
        with self.file.file.open("wb") as handle:
            handle.write(b"<document>a much longer body than before</document>")
        versions.save_version(self.file, author=self.author)

        versions.restore_version(first, actor=self.author)
        self.file.refresh_from_db()

        body = b"<document>one</document>"
        self.assertEqual(self.file.content_hash, hashlib.sha256(body).hexdigest())
        self.assertEqual(self.file.file_size_bytes, len(body))


class ConflictTests(VersionTestCase):
    """A losing writer's work is kept, not merged and not discarded."""

    def test_a_conflicting_draft_keeps_a_body_that_never_reached_the_file(self):
        draft = versions.save_conflicting_draft(
            self.file, body=b"<document>mine</document>", author=self.other)

        self.assertTrue(draft.is_conflict)
        self.assertEqual(draft.read(), b"<document>mine</document>")
        # The live file is untouched — the winner's version still stands.
        self.assertEqual(self._body(), b"<document>one</document>")

    def test_the_draft_names_who_lost_it(self):
        draft = versions.save_conflicting_draft(
            self.file, body=b"x", author=self.other)
        self.assertIn("second-writer", str(draft.label))

    def test_a_conflicting_draft_is_pinned(self):
        draft = versions.save_conflicting_draft(
            self.file, body=b"x", author=self.other)
        self.assertTrue(draft.is_pinned)


class PruneTests(VersionTestCase):
    def setUp(self):
        super().setUp()
        self._cap = versions.UNLABELLED_CAP
        versions.UNLABELLED_CAP = 3
        self.addCleanup(setattr, versions, "UNLABELLED_CAP", self._cap)

    def _save(self, marker: bytes, **kwargs):
        with self.file.file.open("wb") as handle:
            handle.write(marker)
        return versions.save_version(self.file, author=self.author, **kwargs)

    def test_unnamed_versions_past_the_cap_are_dropped_oldest_first(self):
        for n in range(5):
            self._save(f"<document>{n}</document>".encode())

        kept = list(FileVersion.objects.filter(file=self.file)
                    .order_by("number").values_list("number", flat=True))
        self.assertEqual(kept, [3, 4, 5])

    def test_a_named_version_is_never_pruned(self):
        # Somebody decided this one mattered. Silently deleting it would break
        # the only promise the feature makes.
        named = self._save(b"<document>keep me</document>", label="before the rewrite")
        for n in range(6):
            self._save(f"<document>{n}</document>".encode())

        self.assertTrue(
            FileVersion.objects.filter(pk=named.pk).exists())

    def test_named_versions_do_not_count_against_the_cap(self):
        for n in range(4):
            self._save(f"<document>named-{n}</document>".encode(), label=f"v{n}")
        for n in range(3):
            self._save(f"<document>plain-{n}</document>".encode())

        self.assertEqual(
            FileVersion.objects.filter(file=self.file, label="").count(), 3)
        self.assertEqual(
            FileVersion.objects.filter(file=self.file).exclude(label="").count(), 4)

    def test_a_conflicting_draft_is_never_pruned(self):
        draft = versions.save_conflicting_draft(
            self.file, body=b"<document>rescued</document>", author=self.other)
        for n in range(6):
            self._save(f"<document>{n}</document>".encode())

        self.assertTrue(FileVersion.objects.filter(pk=draft.pk).exists())

    def test_a_blob_survives_while_any_version_still_cites_it(self):
        # Two versions of identical content share one blob; pruning one must
        # not pull the bytes out from under the other. The FK is PROTECT, so
        # getting this wrong is an error rather than data loss.
        shared = b"<document>shared</document>"
        keeper = self._save(shared, label="keep")
        self._save(shared)
        for n in range(5):
            self._save(f"<document>{n}</document>".encode())

        keeper.refresh_from_db()
        self.assertTrue(VersionBlob.objects.filter(pk=keeper.blob_id).exists())
        self.assertEqual(keeper.read(), shared)

    def test_an_orphaned_blob_is_removed_with_its_last_version(self):
        for n in range(5):
            self._save(f"<document>{n}</document>".encode())

        # 5 distinct bodies, 3 versions survive → 3 blobs remain.
        self.assertEqual(VersionBlob.objects.count(), 3)


class ListingTests(VersionTestCase):
    def test_versions_come_back_newest_first(self):
        for n in range(3):
            with self.file.file.open("wb") as handle:
                handle.write(f"<document>{n}</document>".encode())
            versions.save_version(self.file, author=self.author)

        listed = versions.list_versions(self.file)
        self.assertEqual([v.number for v in listed], [3, 2, 1])

    def test_one_files_versions_never_leak_into_another(self):
        other_file = self._make_file(b"<document>other</document>",
                                     title="Other", key="other")
        versions.save_version(self.file, author=self.author)
        versions.save_version(other_file, author=self.author)

        self.assertEqual(len(versions.list_versions(self.file)), 1)
        self.assertEqual(versions.list_versions(other_file)[0].number, 1)
