"""Folders that update as they change (2026-10-04): what the vault publishes,
who may watch a folder and what it lists for them, the row door and the
thumbnail door.

    manage.py test toto.vault.tests_live
"""

import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.core import live as signal
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault import live
from toto.vault.models import Bucket, BucketClearance, VaultDirectory, VaultFile
from toto.vault.signals import changes, file_changed

User = get_user_model()


def png(size=(400, 300), colour=(200, 30, 30, 255), mode="RGBA", fmt="PNG"):
    from PIL import Image

    out = io.BytesIO()
    Image.new(mode, size, colour).save(out, format=fmt)
    return out.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-live-"), LIVE_REDIS_URL="")
class Case(TestCase):
    def setUp(self):
        cache.clear()
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        self.owner = User.objects.create_user("owner", password="pw")
        self.reader = User.objects.create_user("reader", password="pw")
        self.bucket = Bucket.objects.create(name="Work", slug="work", owner=self.owner)
        self.folder = VaultDirectory.objects.create(name="Papers", bucket=self.bucket,
                                                    owner=self.owner)
        self.other = VaultDirectory.objects.create(name="Other", bucket=self.bucket,
                                                   owner=self.owner)
        self.client.force_login(self.owner)

    _n = 0

    def file(self, title="note.txt", body=b"x", *, file_type="text", folder="default",
             public=False, owner=None, capture=True, **extra):
        type(self)._n += 1
        vault_file = VaultFile(owner=owner or self.owner, title=title, key=f"k{self._n}",
                               file_type=file_type, bucket=self.bucket, is_public=public,
                               directory=self.folder if folder == "default" else folder, **extra)
        vault_file.file.save(title, ContentFile(body), save=False)
        with self.captureOnCommitCallbacks(execute=capture):
            vault_file.save()
        return VaultFile.objects.get(pk=vault_file.pk)

    def stamps(self):
        keys = [live.folder_key(self.folder.pk), live.folder_key(self.other.pk)]
        found = signal.stamps(keys)
        return found[keys[0]], found[keys[1]]

    def act(self, function):
        """Which of the two folders got a new stamp: ``(Papers, Other)``."""
        before = self.stamps()
        with self.captureOnCommitCallbacks(execute=True):
            function()
        after = self.stamps()
        return after[0] != before[0], after[1] != before[1]


class EventTests(Case):
    def test_an_upload_says_its_folder_changed_and_nothing_else(self):
        self.assertEqual(self.stamps(), ("", ""))
        self.assertEqual(self.act(lambda: self.file("secret-plan.txt", capture=False)),
                         (True, False))
        stamp = self.stamps()[0]
        self.assertTrue(stamp)
        self.assertNotIn("secret", stamp)
        self.assertEqual(live.folder_key(self.folder.pk), f"folder.{self.folder.pk}")

    def test_nothing_is_said_for_a_change_that_rolled_back(self):
        with self.captureOnCommitCallbacks(execute=False):
            self.file(capture=False)
        self.assertEqual(self.stamps(), ("", ""))

    def test_rename_replace_trash_restore(self):
        vault_file = self.file()

        def rename():
            vault_file.title = "other.txt"
            vault_file.save(update_fields=["title"])
        self.assertEqual(self.act(rename), (True, False))
        VaultFile.objects.filter(pk=vault_file.pk).update(content_hash="a" * 64)
        fresh = VaultFile.objects.get(pk=vault_file.pk)

        def replace():
            fresh.content_hash = "b" * 64
            fresh.save(update_fields=["content_hash"])
        self.assertEqual(self.act(replace), (True, False))
        self.assertEqual(
            self.act(lambda: VaultFile.objects.get(pk=vault_file.pk).trash(by=self.owner)),
            (True, False))
        from toto.vault.trash import restore_file

        self.assertEqual(
            self.act(lambda: restore_file(VaultFile.all_objects.get(pk=vault_file.pk))),
            (True, False))

    def test_a_move_touches_the_folder_it_left_and_the_one_it_reached(self):
        vault_file = self.file()

        def move():
            vault_file.directory = self.other
            vault_file.save(update_fields=["directory"])
        self.assertEqual(self.act(move), (True, True))
        self.assertEqual(live.folders_of("moved", vault_file, {"directory_id": self.folder.pk}),
                         [self.folder.pk, self.other.pk])

    def test_a_file_at_a_buckets_top_and_a_save_that_changes_nothing_say_nothing(self):
        self.assertEqual(self.act(lambda: self.file(folder=None, capture=False)), (False, False))
        vault_file = self.file()
        self.assertEqual(self.act(lambda: vault_file.save(update_fields=["notes"])),
                         (False, False))
        self.assertEqual(self.act(vault_file.save), (False, False))

    def test_a_delete_outright_touches_its_folder_and_a_trashed_rows_purge_does_not(self):
        vault_file = self.file()
        self.assertEqual(self.act(vault_file.delete), (True, False))
        trashed = self.file()
        trashed.trash()
        self.assertEqual(self.act(VaultFile.all_objects.get(pk=trashed.pk).delete),
                         (False, False))

    def test_the_kinds_of_one_save(self):
        base = {"directory_id": 1, "bucket_id": 1, "title": "a", "file_type": "text",
                "content_hash": "", "is_encrypted": False, "is_public": False,
                "owner_id": 1, "trashed_at": None}
        self.assertEqual(changes(True, {}, base), ["uploaded"])
        self.assertEqual(changes(False, {}, base), [])
        self.assertEqual(changes(False, base, {**base, "content_hash": "h"}), [])
        self.assertEqual(changes(False, base, {**base, "title": "b"}, {"notes"}), [])
        self.assertEqual(changes(False, base, {**base, "title": "b", "directory_id": 2}),
                         ["moved", "changed"])

    def test_a_listener_that_fails_never_fails_the_save(self):
        def broken(**kwargs):
            raise RuntimeError("no")
        file_changed.connect(broken)
        try:
            with self.assertLogs("toto.vault", "WARNING"):
                self.file()
        finally:
            file_changed.disconnect(broken)


class AccessTests(Case):
    def test_watched_is_the_listings_rule(self):
        self.assertTrue(live.may_watch(self.reader, self.folder.pk))
        self.assertFalse(live.may_watch(self.reader, 999999))
        self.assertFalse(live.may_watch(self.reader, "x"))
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(live.may_watch(AnonymousUser(), self.folder.pk))
        self.other.allowed_users.add(self.owner)
        self.assertFalse(live.may_watch(self.reader, self.other.pk))
        asked = [self.other.pk, "x", 999999, self.folder.pk, self.folder.pk]
        self.assertEqual(live.watched(self.reader, asked), [self.folder.pk])
        self.assertEqual(live.watched(self.owner, asked), [self.other.pk, self.folder.pk])
        # One rule, not two: what the folder's own method says.
        for user in (self.owner, self.reader, User.objects.create_superuser("root", "", "pw")):
            for folder in (self.folder, self.other):
                with self.subTest(user=user.username, folder=folder.name):
                    self.assertEqual(live.may_watch(user, folder.pk),
                                     folder.user_can_access(user))
        BucketClearance.objects.create(bucket=self.bucket,
                                       clearance=Clearance.objects.create(name="Payroll"))
        Person.objects.create(user=self.owner, display_name="Owner")
        self.assertFalse(live.may_watch(self.owner, self.folder.pk))    # no owner bypass
        self.assertEqual(live.watched(self.owner, asked), [])

    def test_the_rows_of_a_folder_are_the_ones_the_list_shows_the_reader(self):
        private, public = self.file(), self.file(public=True)
        elsewhere, trashed = self.file(folder=self.other, public=True), self.file(public=True)
        trashed.trash()
        mine = live.rows(self.owner, self.folder.pk)
        self.assertEqual(sorted(mine), sorted([str(private.pk), str(public.pk)]))
        self.assertEqual(list(live.rows(self.reader, self.folder.pk)), [str(public.pk)])
        self.assertNotIn(str(elsewhere.pk), mine)
        # ids and a tag: no name, and the tag is the row's own.
        self.assertEqual(mine[str(public.pk)], live.row_version(public))
        self.assertNotIn("note", str(mine))
        self.assertRegex(mine[str(public.pk)], r"^[0-9a-f]{10}$")

    def test_the_tag_changes_when_the_row_does(self):
        vault_file = self.file("photo.png")
        before = live.row_version(vault_file)
        self.assertEqual(before, live.row_version(VaultFile.objects.get(pk=vault_file.pk)))
        vault_file.title = "renamed.png"
        self.assertNotEqual(live.row_version(vault_file), before)
        vault_file.title, vault_file.content_hash = "photo.png", "b" * 64
        self.assertNotEqual(live.row_version(vault_file), before)

    def test_a_folder_too_large_to_compare_is_not_listed(self):
        from unittest import mock

        self.file(), self.file()
        with mock.patch.object(live, "MAX_ROWS", 1):
            self.assertIsNone(live.rows(self.owner, self.folder.pk))


class RowDoorTests(Case):
    def row(self, vault_file, user=None):
        client = self.client
        if user is not None:
            client = Client()
            client.force_login(user)
        return client.get(reverse("vault:file_row", args=[vault_file.pk]))

    def test_it_answers_the_row_the_listing_draws(self):
        vault_file = self.file("note.txt")
        response = self.row(vault_file)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        listed = [i for i in self.client.get(reverse("vault:public_list")).context["flat_items"]
                  if i["t"] == "file" and i["id"] == vault_file.pk][0]
        item = response.json()["item"]
        self.assertEqual({k: v for k, v in item.items() if k != "depth"},
                         {k: v for k, v in listed.items() if k != "depth"})
        self.assertEqual(item["pid"], self.folder.pk)
        # The tag the long poll's answer is compared with.
        self.assertEqual(item["v"], live.rows(self.owner, self.folder.pk)[str(vault_file.pk)])

    def test_a_file_the_reader_is_not_shown_is_a_404_like_one_that_is_not_there(self):
        private, public = self.file(), self.file(public=True)
        trashed = self.file()
        trashed.trash()
        self.assertEqual(self.row(private, self.reader).status_code, 404)
        self.assertEqual(self.row(public, self.reader).status_code, 200)
        self.assertEqual(self.row(trashed).status_code, 404)
        self.assertEqual(self.client.get(reverse("vault:file_row", args=[999999])).status_code, 404)
        self.assertEqual(self.client.post(reverse("vault:file_row", args=[public.pk])).status_code, 405)

    def test_a_kept_bucket_hides_the_row_from_its_owner_too(self):
        vault_file = self.file(public=True)
        BucketClearance.objects.create(bucket=self.bucket,
                                       clearance=Clearance.objects.create(name="Payroll"))
        Person.objects.create(user=self.owner, display_name="Owner")
        self.assertEqual(self.row(vault_file).status_code, 404)
        self.assertEqual(self.row(vault_file, self.reader).status_code, 404)


class ThumbnailTests(Case):
    def thumb(self, vault_file, user=None):
        client = self.client
        if user is not None:
            client = Client()
            client.force_login(user)
        return client.get(reverse("vault:file_thumb", args=[vault_file.pk]))

    def test_a_raster_picture_gets_a_small_one_drawn_again(self):
        from PIL import Image

        vault_file = self.file("photo.png", png(), file_type="image")
        item = self.row_item(vault_file)
        self.assertTrue(item["thumb_url"].startswith(reverse("vault:file_thumb", args=[vault_file.pk])))
        response = self.thumb(vault_file)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", response["Content-Security-Policy"])
        self.assertTrue(response["Cache-Control"].startswith("private"))
        self.assertNotIn("Content-Disposition", response)
        small = Image.open(io.BytesIO(response.content))
        self.assertEqual(max(small.size), 192)
        self.assertNotEqual(response.content, png())
        jpeg = self.file("photo.jpg", png(mode="RGB", colour=(1, 2, 3), fmt="JPEG"),
                         file_type="image")
        self.assertEqual(self.thumb(jpeg)["Content-Type"], "image/jpeg")

    def row_item(self, vault_file):
        return self.client.get(reverse("vault:file_row", args=[vault_file.pk])).json()["item"]

    def test_nothing_else_is_drawn(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        cases = {
            "an svg": self.file("drawing.svg", svg, file_type="svg"),
            "an svg called a picture": self.file("drawing.png", svg, file_type="image"),
            "a text file": self.file("note.txt"),
            "a locked picture": self.file("locked.png", png(), file_type="image",
                                          is_encrypted=True),
            "a bitmap": self.file("old.bmp", png(mode="RGB", colour=(1, 2, 3), fmt="BMP"),
                                  file_type="image"),
        }
        for what, vault_file in cases.items():
            with self.subTest(what=what):
                self.assertEqual(self.thumb(vault_file).status_code, 404)
        self.assertEqual(self.row_item(cases["an svg"])["thumb_url"], "")
        self.assertEqual(self.row_item(cases["a text file"])["thumb_url"], "")
        self.assertEqual(self.client.get(reverse("vault:file_thumb", args=[999999])).status_code, 404)

    def test_only_for_somebody_who_may_read_the_file_cache_or_not(self):
        vault_file = self.file("photo.png", png(), file_type="image")
        self.assertEqual(self.thumb(vault_file).status_code, 200)       # now in the cache
        self.assertEqual(self.thumb(vault_file, self.reader).status_code, 404)
        self.assertIn(Client().get(reverse("vault:file_thumb", args=[vault_file.pk])).status_code,
                      (302, 404))
        vault_file.trash()
        self.assertEqual(self.thumb(vault_file).status_code, 404)


class PageTests(Case):
    def test_the_page_carries_the_doors_as_data_and_draws_small_pictures(self):
        self.file("photo.png", png(), file_type="image")
        response = self.client.get(reverse("vault:public_list"))
        body = response.content.decode()
        self.assertTrue(response.context["vault_live"])
        self.assertIn(f'data-row-url="{reverse("vault:file_row", args=[0])}"', body)
        self.assertIn(f'data-upload-url="{reverse("vault:api_file_upload")}"', body)
        self.assertIn('data-live="1"', body)
        self.assertIn("row.v !== listed[key]", body)
        self.assertIn(f'data-wait-url="{reverse("notify:api_wait")}"', body)
        for gone in ("WebSocket", "ws/live", "wss:"):
            self.assertNotIn(gone, body)
        self.assertIn(':src="item.thumb_url"', body)
        self.assertIn("vault/upload_panel.js", body)
        self.assertIn('data-vault-control="upload-panel"', body)
        for marker in ("<iframe", "<object", "<embed", "<video", "<audio", "openImageModal(item)\""):
            self.assertNotIn(marker, body)
        self.assertNotIn("window.location.reload();\n          if (refused", body)
