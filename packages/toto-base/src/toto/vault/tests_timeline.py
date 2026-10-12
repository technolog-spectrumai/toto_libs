"""The timeline of a file and of a bucket (``timeline.py``, 2026-10-12): what
writes a row, what a row holds, and who reads it. A file's history is who
created, edited, opened or downloaded it, and when; a bucket's holds the
rest too."""

from __future__ import annotations

import io
import tempfile

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Clearance

from . import bucket_lifecycle, clearances, timeline
from .models import Bucket, BucketCopyLog, VaultDirectory, VaultEvent, VaultFile

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-timeline-"))
class Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.keeper = User.objects.create_user("keeper", password="pw")
        cls.guest = User.objects.create_user("guest", password="pw")
        cls.outsider = User.objects.create_user("outsider", password="pw")
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        cls.bucket = Bucket.objects.create(name="Work", slug="work", owner=cls.keeper)
        cls.other = Bucket.objects.create(name="Archive", slug="archive", owner=cls.keeper)
        cls.inbox = VaultDirectory.objects.create(name="Inbox", bucket=cls.bucket, owner=cls.keeper)
        cls.done = VaultDirectory.objects.create(name="Done", bucket=cls.bucket, owner=cls.keeper)

    def make(self, title="note.txt", owner=None, directory=None, bucket=None, **more):
        return VaultFile.objects.create(
            owner=owner or self.guest, title=title, key=title.replace(".", "-"),
            file_type="text", bucket=bucket or self.bucket,
            directory=self.inbox if directory is None else directory,
            file=SimpleUploadedFile(title, b"x"), **more)

    def kinds(self, vault_file=None, bucket=None):
        rows = VaultEvent.objects.all()
        if vault_file is not None:
            rows = rows.filter(file_number=vault_file.pk)
        if bucket is not None:
            rows = rows.filter(bucket=bucket)
        return [event.kind for event in rows.order_by("at", "pk")]

    def last(self, kind):
        return VaultEvent.objects.filter(kind=kind).order_by("-pk").first()


class WritingTests(Fixture):
    def test_a_new_file_is_created_by_its_uploader(self):
        note = self.make()
        event = VaultEvent.objects.get(file_number=note.pk)
        self.assertEqual((event.kind, event.file, event.bucket, event.file_title),
                         (timeline.CREATED, note, self.bucket, "note.txt"))
        self.assertEqual((event.actor, event.actor_name), (self.guest, "guest"))
        self.assertEqual(event.details, {"folder": "Inbox"})

    def test_what_a_save_changes_is_what_is_recorded(self):
        note = self.make()
        note.title = "letter.txt"
        note.save()
        note.is_public = True
        note.save()
        note.is_encrypted = True
        note.save()
        note.is_encrypted = False
        note.is_public = False
        note.save()
        note.directory = self.done
        note.save()
        self.assertEqual(self.kinds(note), [
            timeline.CREATED, timeline.RENAMED, timeline.PUBLISHED, timeline.ENCRYPTED,
            timeline.DECRYPTED, timeline.UNPUBLISHED, timeline.MOVED])
        self.assertEqual(self.last(timeline.RENAMED).details, {"from": "note.txt", "to": "letter.txt"})
        self.assertEqual(self.last(timeline.MOVED).details, {"from": "Inbox", "to": "Done"})
        # A save that changes nothing of the kind writes nothing.
        note.save()
        self.assertEqual(len(self.kinds(note)), 7)

    def test_a_move_to_another_bucket_is_a_row_in_each(self):
        note = self.make()
        note.bucket = self.other
        note.directory = None
        note.save()
        self.assertEqual(self.kinds(bucket=self.bucket), [timeline.CREATED, timeline.MOVED_OUT])
        self.assertEqual(self.kinds(bucket=self.other), [timeline.MOVED])
        self.assertEqual(self.last(timeline.MOVED_OUT).details, {"to_bucket": "Archive"})
        self.assertEqual(self.last(timeline.MOVED).details, {"from_bucket": "Work"})

    def test_the_trash_and_back_and_the_purge(self):
        note = self.make()
        note.trashed_at = timezone.now()
        note.trashed_by = self.keeper
        note.trashed_from = note.directory
        note.directory = None
        note.save()
        trashed = self.last(timeline.TRASHED)
        self.assertEqual((trashed.actor, trashed.details), (self.keeper, {"folder": "Inbox"}))
        note.trashed_at = None
        note.directory = self.inbox
        note.save()
        self.assertEqual(self.kinds(note), [timeline.CREATED, timeline.TRASHED, timeline.RESTORED])
        timeline.purged(note, self.root)
        self.assertEqual((self.last(timeline.PURGED).actor_name, self.kinds(note)[-1]),
                         ("root", timeline.PURGED))

    def test_a_file_that_is_gone_keeps_its_history(self):
        note = self.make()
        number = note.pk
        note.delete()
        rows = VaultEvent.objects.filter(file_number=number).order_by("at", "pk")
        self.assertEqual([event.kind for event in rows], [timeline.CREATED, timeline.DELETED])
        self.assertEqual({(event.file, event.file_title) for event in rows}, {(None, "note.txt")})

    def test_a_mirrored_row_is_nobodys_act(self):
        self.make(title="far.txt", origin="mirror")
        self.assertFalse(VaultEvent.objects.exists())

    def test_the_one_at_the_keyboard_is_who_did_it(self):
        if not apps.is_installed("toto.audit"):
            self.skipTest("the actor of a request is the audit context's")
        note = self.make(owner=self.keeper)
        self.client.force_login(self.keeper)
        response = self.client.post(reverse("vault:rename_file"),
                                    {"file_pk": note.pk, "title": "renamed.txt"})
        self.assertEqual(response.status_code, 200, response.content[:200])
        renamed = self.last(timeline.RENAMED)
        self.assertEqual((renamed.actor, renamed.details["to"]), (self.keeper, "renamed.txt"))

    def test_what_happens_to_a_bucket_itself(self):
        made = Bucket.objects.create(name="Fresh", slug="fresh", owner=self.keeper)
        bucket_lifecycle.record_created(made, self.root)
        bucket_lifecycle.update_bucket(made, self.root, name="Fresher")
        self.assertEqual(self.kinds(bucket=made), [timeline.BUCKET_CREATED, timeline.BUCKET_CHANGED])
        self.assertEqual(self.last(timeline.BUCKET_CHANGED).details, {"fields": "name"})
        self.assertEqual(self.last(timeline.BUCKET_CREATED).actor_name, "root")

    def test_clearances_and_copies(self):
        internal = Clearance.objects.create(name="internal", slug="internal")
        clearances.set_clearances(self.bucket, [internal], actor=self.root)
        clearances.set_clearances(self.bucket, [internal], actor=self.root)      # nothing changed
        changed = VaultEvent.objects.filter(kind=timeline.CLEARANCES_CHANGED)
        self.assertEqual([event.details for event in changed], [{"after": "internal"}])
        BucketCopyLog.objects.create(from_bucket=self.bucket, to_bucket=self.other,
                                     performed_by=self.keeper, file_count=3)
        self.assertEqual(self.last(timeline.COPIED_OUT).details, {"count": 3, "bucket": "Archive"})
        self.assertEqual((self.last(timeline.COPIED_IN).bucket, self.last(timeline.COPIED_IN).details),
                         (self.other, {"count": 3, "bucket": "Work"}))

    def test_a_row_that_cannot_be_written_never_breaks_the_change(self):
        from unittest import mock

        with mock.patch.object(VaultEvent.objects, "create", side_effect=RuntimeError("no")), \
                self.assertLogs("toto.vault", level="ERROR"):
            note = self.make()
        self.assertTrue(VaultFile.objects.filter(pk=note.pk).exists())

    def test_an_erased_account_leaves_its_rows_nobodys(self):
        gone = User.objects.create_user("gone", password="pw")
        note = self.make(owner=self.keeper)
        timeline.record(timeline.EDITED, bucket_id=self.bucket.pk, file=note, actor=gone)
        gone.delete()
        event = self.last(timeline.EDITED)
        self.assertEqual((event.actor, event.actor_name), (None, ""))

    def test_a_bucket_takes_its_history_with_it(self):
        doomed = Bucket.objects.create(name="Doomed", slug="doomed", owner=self.keeper)
        timeline.record(timeline.BUCKET_CREATED, bucket_id=doomed.pk, actor=self.root)
        doomed.delete()
        self.assertFalse(VaultEvent.objects.filter(kind=timeline.BUCKET_CREATED).exists())


class ReadTests(Fixture):
    """Who opened or downloaded a file, and when."""

    def download(self, note, user, **headers):
        self.client.force_login(user)
        return self.client.get(reverse("vault:public_file", args=[note.bucket.slug, note.key]),
                               **headers)

    def setUp(self):
        if not apps.is_installed("toto.audit"):
            self.skipTest("who reads is the audit context's")

    def test_a_download_is_a_row_with_who_and_when(self):
        note = self.make()
        response = self.download(note, self.keeper)
        self.assertEqual(response.status_code, 200)
        b"".join(response.streaming_content)
        row = VaultEvent.objects.get(kind=timeline.DOWNLOADED)
        self.assertEqual((row.actor, row.actor_name, row.file, row.bucket),
                         (self.keeper, "keeper", note, self.bucket))
        self.assertEqual(self.kinds(note), [timeline.CREATED, timeline.DOWNLOADED])

    def test_a_file_shown_in_the_page_is_opened_not_downloaded(self):
        note = self.make(title="photo.png")
        b"".join(self.download(note, self.keeper, HTTP_SEC_FETCH_DEST="image").streaming_content)
        self.assertEqual(self.kinds(note), [timeline.CREATED, timeline.OPENED])
        # And a door that serves for display says so itself.
        from .views import stream_file

        other = self.make(title="page.txt")
        from toto.audit.context import AuditContext, reset_context, set_context

        before = VaultEvent.objects.count()
        token = set_context(AuditContext(user=self.guest, request=None, source="test"))
        try:
            stream_file(other, inline=True).close()
        finally:
            reset_context(token)
        self.assertEqual(VaultEvent.objects.count(), before + 1)
        self.assertEqual(self.last(timeline.OPENED).actor, self.guest)

    def test_the_same_reader_again_soon_is_one_row(self):
        note = self.make()
        for _again in range(3):
            b"".join(self.download(note, self.keeper).streaming_content)
        b"".join(self.download(note, self.root).streaming_content)
        rows = VaultEvent.objects.filter(kind=timeline.DOWNLOADED)
        self.assertEqual(sorted(rows.values_list("actor_name", flat=True)), ["keeper", "root"])
        # Past the fold a new read is a new row.
        from datetime import timedelta

        rows.filter(actor=self.keeper).update(
            at=timezone.now() - timedelta(minutes=timeline.READ_FOLD_MINUTES + 1))
        b"".join(self.download(note, self.keeper).streaming_content)
        self.assertEqual(rows.filter(actor=self.keeper).count(), 2)

    def test_a_refused_read_and_a_read_by_nobody_are_not_rows(self):
        note = self.make()
        self.assertIn(self.download(note, self.outsider).status_code, (403, 404))
        self.assertIsNone(timeline.read(note))                    # no request: a worker, the console
        self.assertEqual(self.kinds(note), [timeline.CREATED])

    def test_the_history_shows_the_reader_to_whoever_may_read_the_file(self):
        note = self.make()
        b"".join(self.download(note, self.keeper).streaming_content)
        self.client.force_login(self.guest)
        rows = self.client.get(reverse("vault:file_history", args=[note.pk])).context["rows"]
        self.assertEqual([(row["who"], row["what"]) for row in rows],
                         [("keeper", "Downloaded"), ("guest", "Created")])
        self.assertEqual(set(rows[0]), {"id", "kind", "icon", "what", "who", "file",
                                        "file_number", "when", "at"})


class BackfillTests(Fixture):
    def test_a_file_from_before_the_timeline_gets_its_created_row(self):
        import importlib

        from django.apps import apps as live

        old = self.make(title="old.txt", owner=self.keeper)
        kept = self.make(title="kept.txt")
        VaultEvent.objects.filter(file_number=old.pk).delete()         # as if made before
        long_ago = timezone.now() - timezone.timedelta(days=400)
        VaultFile.all_objects.filter(pk=old.pk).update(uploaded_at=long_ago)
        migration = importlib.import_module("toto.vault.migrations.0003_vaultevent_backfill")
        migration.backfill(live, None)
        migration.backfill(live, None)                                 # twice: once there
        row = VaultEvent.objects.get(file_number=old.pk)
        self.assertEqual((row.kind, row.actor, row.actor_name, row.at),
                         ("created", self.keeper, "keeper", long_ago))
        self.assertEqual(VaultEvent.objects.filter(file_number=kept.pk).count(), 1)


class SentenceTests(Fixture):
    def test_a_files_history_is_four_kinds_and_no_more(self):
        self.assertEqual(timeline.HISTORY_KINDS, ("created", "edited", "opened", "downloaded"))
        note = self.make()
        for kind in timeline.FILE_KINDS:
            timeline.record(kind, bucket_id=self.bucket.pk, file=note, actor=self.keeper)
        self.assertEqual(sorted(set(timeline.of_file(note).values_list("kind", flat=True))),
                         ["created", "downloaded", "edited", "opened"])

    def test_every_kind_has_a_sentence_and_a_filter_label(self):
        named = dict(timeline.kinds_named())
        self.assertEqual(set(named), set(timeline.FILE_KINDS + timeline.BUCKET_KINDS))
        with translation.override("en"):
            for kind in timeline.FILE_KINDS + timeline.BUCKET_KINDS:
                event = VaultEvent(bucket=self.bucket, kind=kind, details={})
                self.assertNotEqual(timeline.sentence(event), kind, kind)
                self.assertIn(kind, timeline.ICONS)
            renamed = VaultEvent(kind=timeline.RENAMED, details={"from": "a.txt", "to": "b.txt"})
            self.assertEqual(timeline.sentence(renamed), "Renamed from a.txt to b.txt")
            moved = VaultEvent(kind=timeline.MOVED, details={"from": "", "to": "Done"})
            self.assertEqual(timeline.sentence(moved), "Moved from the bucket's top to Done")

    def test_a_row_is_plain_and_holds_no_address(self):
        note = self.make()
        row = timeline.row(VaultEvent.objects.get(file_number=note.pk))
        self.assertEqual(set(row), {"id", "kind", "icon", "what", "who", "file", "file_number",
                                    "when", "at"})
        self.assertEqual((row["who"], row["file"], row["kind"], row["what"]),
                         ("guest", "note.txt", "created", "Created"))
        self.assertEqual({field.name for field in VaultEvent._meta.get_fields()},
                         {"id", "bucket", "file", "file_number", "file_title", "kind", "actor",
                          "actor_name", "details", "at"})


class FileDoorTests(Fixture):
    def door(self, vault_file, user, **query):
        self.client.force_login(user)
        return self.client.get(reverse("vault:file_history", args=[vault_file.pk]), query)

    def test_whoever_may_read_the_file_reads_its_history(self):
        note = self.make()                                    # the guest's, in the keeper's bucket
        note.title = "letter.txt"                             # a rename is the bucket's to tell
        note.save()
        note.content_hash = "a" * 64
        note.save()
        note.content_hash = "b" * 64                          # the hash moved: an edit
        note.save()
        for reader in (self.guest, self.keeper, self.root):
            response = self.door(note, reader)
            self.assertEqual(response.status_code, 200, reader.username)
            rows = response.context["rows"]
            self.assertEqual([(row["kind"], row["what"]) for row in rows],
                             [("edited", "Edited"), ("created", "Created")])
            self.assertEqual((response.context["total"], response.context["is_paginated"]),
                             (2, False))
            self.assertEqual(rows[-1]["who"], "guest")
            body = response.content.decode()
            self.assertIn("History of letter.txt", body)
            self.assertEqual(body.count('data-testid="file-history-row"'), 2)
            # Back to where the file is: its bucket's files.
            self.assertIn(f'{reverse("vault:public_list")}?bucket={self.bucket.slug}', body)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertTemplateUsed(response, "vault/file_history.html")

    def test_somebody_who_may_not_read_it_gets_what_a_missing_file_gets(self):
        note = self.make()
        self.assertEqual(self.door(note, self.outsider).status_code, 404)
        self.client.logout()
        self.assertIn(self.client.get(reverse("vault:file_history", args=[note.pk])).status_code,
                      (302, 403, 404))
        self.client.force_login(self.keeper)
        self.assertEqual(self.client.get(reverse("vault:file_history", args=[987654])).status_code, 404)
        # A public file's history is every member's, as the file is.
        note.is_public = True
        note.save()
        self.assertEqual(self.door(note, self.outsider).status_code, 200)

    def test_a_bucket_kept_to_clearances_keeps_the_history_too(self):
        note = self.make()
        internal = Clearance.objects.create(name="internal", slug="internal")
        clearances.set_clearances(self.bucket, [internal], actor=self.root)
        for kept_out in (self.guest, self.keeper):                    # the owners themselves
            self.assertEqual(self.door(note, kept_out).status_code, 404, kept_out.username)
        Person.objects.create(user=self.guest, display_name="Guest").clearances.add(internal)
        self.assertEqual(self.door(note, self.guest).status_code, 200)
        self.assertEqual(self.door(note, self.root).status_code, 200)

    def test_a_trashed_file_still_answers_and_a_long_history_is_paged(self):
        note = self.make(owner=self.keeper)
        for n in range(timeline.FILE_PAGE + 4):
            timeline.record(timeline.EDITED, bucket_id=self.bucket.pk, file=note, actor=self.keeper)
        first = self.door(note, self.keeper)
        self.assertEqual((len(first.context["rows"]), first.context["total"]),
                         (timeline.FILE_PAGE, timeline.FILE_PAGE + 5))
        self.assertTrue(first.context["is_paginated"])
        body = first.content.decode()
        self.assertEqual(body.count('data-testid="file-history-row"'), timeline.FILE_PAGE)
        self.assertIn("?page=2", body)                              # the platform's pager
        second = self.door(note, self.keeper, page=2)
        self.assertEqual(len(second.context["rows"]), 5)
        self.assertEqual(second.context["rows"][-1]["kind"], "created")
        # Past the end is the last page, not an error.
        self.assertEqual(len(self.door(note, self.keeper, page=99).context["rows"]), 5)
        VaultFile.all_objects.filter(pk=note.pk).update(trashed_at=timezone.now())
        trashed = self.door(note, self.keeper)
        self.assertEqual(trashed.status_code, 200)
        self.assertIn(reverse("vault:trash"), trashed.content.decode())

    def test_the_door_only_reads(self):
        note = self.make()
        self.client.force_login(self.keeper)
        self.assertEqual(self.client.post(reverse("vault:file_history", args=[note.pk])).status_code, 405)


class BucketPageTests(Fixture):
    def page(self, user, bucket=None, **query):
        self.client.force_login(user)
        return self.client.get(reverse("vault:bucket_history",
                                       args=[(bucket or self.bucket).slug]), query)

    def test_the_owner_and_a_superuser_read_the_buckets_history(self):
        note = self.make()
        note.title = "letter.txt"
        note.save()
        timeline.record(timeline.BUCKET_CHANGED, bucket_id=self.bucket.pk, actor=self.root, fields="name")
        for reader in (self.keeper, self.root):
            response = self.page(reader)
            self.assertEqual(response.status_code, 200, reader.username)
            self.assertEqual([row["kind"] for row in response.context["rows"]],
                             ["bucket_changed", "renamed", "created"])
        body = response.content.decode()
        self.assertEqual(body.count('data-testid="bucket-history-row"'), 3)
        self.assertIn("letter.txt", body)
        self.assertIn(reverse("vault:bucket_metrics", args=["work"]), body)
        for control in ("csrfmiddlewaretoken", 'type="submit" name', "<textarea"):
            self.assertNotIn(control, body[body.index('data-testid="bucket-history"'):])

    def test_nobody_else_learns_the_bucket_is_there(self):
        for other in (self.guest, self.outsider):
            self.assertEqual(self.page(other).status_code, 404, other.username)
        self.client.force_login(self.keeper)
        self.assertEqual(self.client.get(reverse("vault:bucket_history", args=["nope"])).status_code, 404)

    def test_one_kind_at_a_time(self):
        note = self.make()
        note.title = "letter.txt"
        note.save()
        response = self.page(self.keeper, kind="renamed")
        self.assertEqual([row["kind"] for row in response.context["rows"]], ["renamed"])
        self.assertIn('<option value="renamed" selected', response.content.decode())
        self.assertEqual(len(self.page(self.keeper, kind="no-such-kind").context["rows"]), 2)

    def test_a_kept_bucket_shows_its_owner_nothing(self):
        self.make()
        internal = Clearance.objects.create(name="internal", slug="internal")
        clearances.set_clearances(self.bucket, [internal], actor=self.root)
        self.assertEqual(self.page(self.keeper).context["rows"], [])
        self.assertTrue(self.page(self.root).context["rows"])

    def test_the_buckets_own_page_has_the_last_of_it_and_the_way_there(self):
        self.make()
        self.client.force_login(self.keeper)
        response = self.client.get(reverse("vault:bucket_metrics", args=["work"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["kind"] for row in response.context["history_rows"]], ["created"])
        body = response.content.decode()
        self.assertIn('id="history"', body)
        self.assertIn(f'href="{reverse("vault:bucket_history", args=["work"])}"', body)


class FileListTests(Fixture):
    def test_the_file_list_offers_history_and_it_goes_to_the_files_page(self):
        """The History action leaves for the file's own page, a table a page
        at a time (2026-10-12): the list draws no history table and has no
        history dialog."""
        self.make(owner=self.keeper)
        self.client.force_login(self.keeper)
        body = self.client.get(reverse("vault:public_list")).content.decode()
        self.assertIn('data-testid="file-action-history"', body)
        self.assertIn(reverse("vault:file_history", args=[0]), body)
        self.assertIn("chooseAction('history')", body)
        self.assertIn("window.location.href = _historyUrlTpl", body)
        for gone in ('data-testid="file-history-modal"', 'data-testid="file-history-table"',
                     "historyModal", "historyRows"):
            self.assertNotIn(gone, body)


class PersonalDataTests(Fixture):
    def test_the_copy_holds_what_the_member_did_and_nobody_elses(self):
        from toto.core import personal_data

        note = self.make()                                       # the guest's upload
        timeline.record(timeline.EDITED, bucket_id=self.bucket.pk, file=note, actor=self.keeper)
        personal_data.plugins()
        with translation.override("en"):
            tables = personal_data.PersonalDataPlugin.get("vault_timeline").tables(self.guest)
        self.assertEqual([table.name for table in tables], ["vault_timeline"])
        self.assertEqual([(row["kind"], row["file"], row["bucket"], row["what"])
                          for row in tables[0].rows],
                         [("created", "note.txt", "Work", "Created")])
