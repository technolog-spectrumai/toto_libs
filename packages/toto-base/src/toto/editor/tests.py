"""The ACE editor's save door: the lock, the precondition, and the history.

`toto` is a PEP 420 namespace package, so this module is named explicitly —
`manage.py test toto.editor` discovers nothing and raises a TypeError. Run it as
`manage.py test toto.editor.tests`.

What is deliberately NOT here: anything about metering. This editor is a
Standard, unmetered feature, which is why `save_file` calls
`versions.save_version` directly instead of `editing.settle` — the latter counts
the save and charges for it.
"""

import hashlib
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.vault import locks
from toto.vault.models import Bucket, FileVersion, VaultFile

User = get_user_model()

# Escapes the strict whitenoise manifest storage, which otherwise needs a real
# collectstatic run before the editor page can render.
storage_override = override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})


@storage_override
class EditorTestCase(TestCase):
    def setUp(self):
        # Both the file's bytes and every version blob are real files, and the
        # deployed MEDIA_ROOT is a root-owned bind mount. One directory per test.
        self._media = tempfile.mkdtemp(prefix="editor-test-")
        self._media_override = override_settings(MEDIA_ROOT=self._media)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(shutil.rmtree, self._media, ignore_errors=True)

        # PageProcessor._get_config raises Http404 without an active Platform,
        # so the editor page 404s instead of rendering.
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)

        self.owner = User.objects.create_user("owner", password="pw")
        self.other = User.objects.create_user("colleague", password="pw")
        self.bucket = Bucket.objects.create(name="Notes", slug="notes",
                                            owner=self.owner,
                                            storage_backend="local")
        self.file = self._make("first\n")
        self.client.force_login(self.owner)

    def _make(self, body: str, *, title="note.txt") -> VaultFile:
        vault_file = VaultFile.objects.create(
            owner=self.owner, title=title, file_type="text", bucket=self.bucket,
            file=SimpleUploadedFile(title, body.encode("utf-8")))
        vault_file.content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        vault_file.save(update_fields=["content_hash"])
        return vault_file

    def _save(self, **payload):
        return self.client.post(
            reverse("editor:text_save", args=[self.file.pk]), payload)

    def _on_disk(self) -> str:
        self.file.refresh_from_db()
        with self.file.file.open("rb") as handle:
            return handle.read().decode("utf-8")


class SaveTests(EditorTestCase):
    def test_a_save_cuts_a_version_and_hands_back_the_new_hash(self):
        """The two halves of a save that lands.

        The hash is not decoration: the client carries it forward as the next
        save's `base_hash`, so a response without it makes the whole
        optimistic-concurrency path unusable from the second save onwards.
        """
        response = self._save(content="second\n")
        self.assertEqual(response.status_code, 200)

        digest = hashlib.sha256(b"second\n").hexdigest()
        self.assertEqual(response.json()["content_hash"], digest)
        self.file.refresh_from_db()
        self.assertEqual(self.file.content_hash, digest)
        self.assertEqual(self._on_disk(), "second\n")

        versions = FileVersion.objects.filter(file=self.file)
        self.assertEqual(versions.count(), 1)
        version = versions.get()
        self.assertEqual(version.number, 1)
        self.assertEqual(version.author, self.owner)
        self.assertFalse(version.is_conflict)
        self.assertEqual(version.read(), b"second\n")
        self.assertEqual(response.json()["version"], 1)

    def test_a_save_without_a_base_hash_still_lands(self):
        """Optional, so nothing that predates this breaks.

        The sketch SVG editor and the desktop API twin post to this view with a
        content field and nothing else. They lose the protection they never had;
        they must not lose the save.
        """
        self.assertEqual(self._save(content="third\n").status_code, 200)
        self.assertEqual(self._on_disk(), "third\n")

    def test_a_get_is_refused(self):
        # @require_POST, in place of the hand-rolled 400 this used to answer.
        self.assertEqual(self.client.get(
            reverse("editor:text_save", args=[self.file.pk])).status_code, 405)


class LockTests(EditorTestCase):
    def test_a_save_is_refused_while_somebody_else_holds_the_lock(self):
        """423, and not one byte written.

        The owner filter on the queryset is not this check: the vault lends a
        file out through other surfaces, so the holder can be a collaborator
        editing through cyprian while the owner sits in this editor.
        """
        locks.acquire(self.file, self.other)

        response = self._save(content="clobbered\n")
        self.assertEqual(response.status_code, 423)
        self.assertEqual(response.json()["locked_by"], "colleague")
        self.assertEqual(self._on_disk(), "first\n")
        self.assertFalse(FileVersion.objects.filter(file=self.file).exists())

    def test_holding_the_lock_yourself_is_not_a_refusal(self):
        # Re-editing your own file, or having it open in two tabs, must never
        # lock you out of it.
        locks.acquire(self.file, self.owner)
        self.assertEqual(self._save(content="mine\n").status_code, 200)


class ConflictTests(EditorTestCase):
    def test_a_stale_save_is_refused_and_the_losing_body_is_kept(self):
        """409, plus the work that would otherwise have vanished.

        Refusing alone is what a user experiences as "it lost my paragraph".
        Both bodies end up in the history and a human decides which one wins.
        """
        response = self._save(content="mine\n", base_hash="not-the-current-one")
        self.assertEqual(response.status_code, 409)

        body = response.json()
        self.file.refresh_from_db()
        self.assertEqual(body["content_hash"], self.file.content_hash)
        self.assertEqual(self._on_disk(), "first\n")

        rescued = FileVersion.objects.get(file=self.file, is_conflict=True)
        self.assertEqual(rescued.read(), b"mine\n")
        self.assertEqual(rescued.number, body["kept_as_version"])
        self.assertTrue(rescued.is_pinned, "a rescued draft must survive pruning")

    def test_a_matching_base_hash_saves(self):
        response = self._save(content="agreed\n", base_hash=self.file.content_hash)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._on_disk(), "agreed\n")
        self.assertFalse(
            FileVersion.objects.filter(file=self.file, is_conflict=True).exists())

    def test_the_hash_a_save_returns_is_good_for_the_next_one(self):
        # The client's whole contract in one test: save, carry the hash, save
        # again. If this ever fails, every second save answers 409.
        first = self._save(content="one\n", base_hash=self.file.content_hash)
        second = self._save(content="two\n",
                            base_hash=first.json()["content_hash"])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self._on_disk(), "two\n")


class EditorPageTests(EditorTestCase):
    """What the page has to carry for any of the above to be reachable."""

    def setUp(self):
        super().setUp()
        self.body = self.client.get(
            reverse("editor:text_display", args=[self.file.pk])).content.decode()

    def test_the_page_claims_the_lock_exactly_once(self):
        # Two instances would claim the lock twice and run two heartbeats
        # against the same file, which is why the banner listens for an event
        # instead of instantiating its own component.
        self.assertIn("oya/file_versions.js", self.body)
        self.assertEqual(self.body.count("fileVersions("), 1)

    def test_the_lock_banner_is_up_by_the_toolbar_not_buried_in_the_panel(self):
        # The versions panel is collapsed, so a message that only lives inside
        # it is a message the person whose save just 423'd never sees.
        self.assertIn("vault-lock", self.body)
        self.assertLess(self.body.index("vault-lock"),
                        self.body.index("fileVersions("))

    def test_the_save_carries_a_base_hash(self):
        # primula's client never sent one, which made its 409 path dead code
        # for a year. Pinned here so this one cannot quietly go the same way.
        self.assertIn("base_hash", self.body)
        self.assertIn(self.file.content_hash, self.body)

    def test_a_conflict_is_a_choice_and_never_a_retry(self):
        # An automatic second attempt is how the other writer's work gets
        # overwritten a second later.
        self.assertIn("conflict-overwrite", self.body)
        self.assertIn("conflict-reload", self.body)


class SyncSocketTests(EditorTestCase):
    """The socket is the writer this app most easily forgets.

    `EditorFileSyncConsumer` rewrites the file on every buffer change — the page
    wires `editor.session.on("change")` straight to it — so it lands long before
    anybody presses Save. A lock enforced only in `save_file` therefore protects
    nothing here: a colleague's held lock would be respected by the button and
    walked straight through by every keystroke.
    """

    def _consumer(self):
        from toto.editor.consumer import EditorFileSyncConsumer

        consumer = EditorFileSyncConsumer()
        consumer.file_pk = self.file.pk
        consumer.user = self.owner
        return consumer

    def test_the_socket_refuses_to_write_under_somebody_else_s_lock(self):
        from asgiref.sync import async_to_sync

        locks.acquire(self.file, self.other)
        holder = async_to_sync(self._consumer()._lock_holder)()
        self.assertEqual(holder, "colleague")

    def test_the_owner_s_own_lock_is_not_a_refusal(self):
        """A file locked by the person typing in it is not locked against them —
        `may_write` refuses only somebody else's live lock, so a second tab of
        your own is layer two's problem and not this one's."""
        from asgiref.sync import async_to_sync

        locks.acquire(self.file, self.owner)
        self.assertEqual(async_to_sync(self._consumer()._lock_holder)(), "")

    def test_an_expired_lock_is_not_a_lock(self):
        from asgiref.sync import async_to_sync
        from django.utils import timezone

        lock = locks.acquire(self.file, self.other)
        lock.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        lock.save(update_fields=["expires_at"])
        self.assertEqual(async_to_sync(self._consumer()._lock_holder)(), "")

    def test_the_socket_reports_the_hash_it_stamped(self):
        """So the client never has to hash the buffer itself.

        The save's precondition has to track this socket's writes, because they
        move `content_hash` on every keystroke. Recomputing it in the browser
        needs SubtleCrypto and therefore a secure context, and would still only
        be a guess at bytes the client never saw written.
        """
        from asgiref.sync import async_to_sync

        verdict, content_hash = async_to_sync(self._consumer().write_file)("second\n")
        self.assertTrue(verdict.ok)
        self.file.refresh_from_db()
        self.assertEqual(content_hash, self.file.content_hash)
        self.assertEqual(
            content_hash,
            hashlib.sha256("second\n".encode("utf-8")).hexdigest())
