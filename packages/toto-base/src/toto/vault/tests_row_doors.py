"""The file list's doors (2026-10-04, 2026-10-06): the row door, the
thumbnail door and the listing door a page asks when its tab is looked at
again; what ``file_changed`` says of a save; and that no page is told of a
change — nothing is published, no folder is watched, nothing waits.

    manage.py test toto.vault.tests_row_doors
"""

import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault.models import Bucket, BucketClearance, VaultDirectory, VaultFile
from toto.vault.signals import changes, file_changed

User = get_user_model()


def png(size=(400, 300), colour=(200, 30, 30, 255), mode="RGBA", fmt="PNG"):
    from PIL import Image

    out = io.BytesIO()
    Image.new(mode, size, colour).save(out, format=fmt)
    return out.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-rows-"))
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

    def said(self, function):
        """The ``file_changed`` kinds ``function`` sends."""
        seen = []

        def listener(sender=None, file=None, kind="", was=None, **kwargs):
            seen.append(kind)

        file_changed.connect(listener)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                function()
        finally:
            file_changed.disconnect(listener)
        return seen


class ChangeSignalTests(Case):
    """``signals.file_changed``: what the bell's source listens to."""

    def test_upload_rename_replace_trash_restore(self):
        self.assertEqual(self.said(lambda: self.file("secret-plan.txt", capture=False)),
                         ["uploaded"])
        vault_file = self.file()

        def rename():
            vault_file.title = "other.txt"
            vault_file.save(update_fields=["title"])
        self.assertEqual(self.said(rename), ["changed"])
        VaultFile.objects.filter(pk=vault_file.pk).update(content_hash="a" * 64)
        fresh = VaultFile.objects.get(pk=vault_file.pk)

        def replace():
            fresh.content_hash = "b" * 64
            fresh.save(update_fields=["content_hash"])
        self.assertEqual(self.said(replace), ["replaced"])
        self.assertEqual(
            self.said(lambda: VaultFile.objects.get(pk=vault_file.pk).trash(by=self.owner)),
            ["trashed"])
        from toto.vault.trash import restore_file

        self.assertEqual(
            self.said(lambda: restore_file(VaultFile.all_objects.get(pk=vault_file.pk))),
            ["restored"])

    def test_a_move_a_save_that_changes_nothing_and_a_delete(self):
        vault_file = self.file()

        def move():
            vault_file.directory = self.other
            vault_file.save(update_fields=["directory"])
        self.assertEqual(self.said(move), ["moved"])
        self.assertEqual(self.said(lambda: vault_file.save(update_fields=["notes"])), [])
        self.assertEqual(self.said(vault_file.save), [])
        self.assertEqual(self.said(vault_file.delete), ["deleted"])
        trashed = self.file()
        trashed.trash()
        self.assertEqual(self.said(VaultFile.all_objects.get(pk=trashed.pk).delete), [])

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


class NoLiveTests(Case):
    """No page is told that a folder changed (2026-10-06)."""

    def test_the_vault_publishes_nothing_and_watches_nothing(self):
        import importlib.util

        self.assertIsNone(importlib.util.find_spec("toto.vault.live"))
        uids = [str(entry[0][0]) for entry in file_changed.receivers]
        self.assertNotIn("toto.vault.live.publish", uids)
        self.assertFalse([uid for uid in uids if ".live" in uid])

    def test_a_change_touches_no_cache_key(self):
        from unittest import mock

        vault_file = self.file()
        with mock.patch("django.core.cache.cache.set") as stamped:
            with self.captureOnCommitCallbacks(execute=True):
                vault_file.title = "renamed.txt"
                vault_file.save(update_fields=["title"])
        self.assertEqual([call for call in stamped.call_args_list
                          if "live" in str(call.args[:1])], [])

    def test_a_row_carries_no_version_tag(self):
        vault_file = self.file()
        item = self.client.get(reverse("vault:file_row", args=[vault_file.pk])).json()["item"]
        self.assertNotIn("v", item)


class ListingDoorTests(Case):
    """``vault:public_list_items``: the page's own list, asked again."""

    def items(self, client=None, **query):
        response = (client or self.client).get(reverse("vault:public_list_items"), query)
        self.assertEqual(response.status_code, 200)
        return response.json()["items"]

    def test_it_answers_exactly_what_the_page_lists(self):
        self.file("note.txt"), self.file("photo.png", png(), file_type="image")
        self.file("deep.txt", folder=self.other), self.file("top.txt", folder=None)
        page = self.client.get(reverse("vault:public_list")).context["flat_items"]
        self.assertEqual(self.items(), page)
        self.assertEqual([i["t"] for i in page].count("file"), 4)
        response = self.client.get(reverse("vault:public_list_items"))
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.client.post(reverse("vault:public_list_items")).status_code, 405)

    def test_it_keeps_to_the_bucket_the_page_shows(self):
        elsewhere = Bucket.objects.create(name="Home", slug="home", owner=self.owner)
        VaultDirectory.objects.create(name="Attic", bucket=elsewhere, owner=self.owner)
        self.file("note.txt")
        page = self.client.get(reverse("vault:public_list"), {"bucket": "work"})
        self.assertEqual(self.items(bucket="work"), page.context["flat_items"])
        self.assertNotIn("Attic", [i.get("name") for i in self.items(bucket="work")])
        self.assertIn("Attic", [i.get("name") for i in self.items()])
        self.assertIn(f'data-items-url="{reverse("vault:public_list_items")}?bucket=work"',
                      page.content.decode())

    def test_a_reader_is_listed_what_a_reload_would_list_them(self):
        private, public = self.file("mine.txt"), self.file("ours.txt", public=True)
        trashed = self.file("gone.txt", public=True)
        trashed.trash()
        reader = Client()
        reader.force_login(self.reader)
        listed = [i["id"] for i in self.items(reader) if i["t"] == "file"]
        self.assertEqual(listed, [public.pk])
        self.assertNotIn("mine.txt", str(self.items(reader)))
        mine = [i["id"] for i in self.items() if i["t"] == "file"]
        self.assertEqual(sorted(mine), sorted([private.pk, public.pk]))
        self.assertEqual(self.items(reader),
                         reader.get(reverse("vault:public_list")).context["flat_items"])

    def test_a_kept_bucket_is_hidden_from_its_owner_too(self):
        self.file("salaries.txt", public=True)
        BucketClearance.objects.create(bucket=self.bucket,
                                       clearance=Clearance.objects.create(name="Payroll"))
        Person.objects.create(user=self.owner, display_name="Owner")
        self.assertNotIn("salaries.txt", str(self.items()))

    def test_somebody_elses_upload_is_there_when_the_page_asks_again(self):
        before = self.items()
        self.file("theirs.txt", public=True, owner=self.reader)
        after = self.items()
        self.assertEqual(len(after), len(before) + 1)
        self.assertIn("theirs.txt", [i.get("title") for i in after])


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
    def body(self):
        self.file("photo.png", png(), file_type="image")
        response = self.client.get(reverse("vault:public_list"))
        self.assertNotIn("vault_live", response.context)
        return response.content.decode()

    def test_the_page_carries_the_doors_as_data_and_draws_small_pictures(self):
        body = self.body()
        self.assertIn(f'data-row-url="{reverse("vault:file_row", args=[0])}"', body)
        self.assertIn(f'data-upload-url="{reverse("vault:api_file_upload")}"', body)
        self.assertIn(f'data-items-url="{reverse("vault:public_list_items")}"', body)
        self.assertIn(':src="item.thumb_url"', body)
        self.assertIn("vault/upload_panel.js", body)
        self.assertIn('data-vault-control="upload-panel"', body)
        for marker in ("<iframe", "<object", "<embed", "<video", "<audio", "openImageModal(item)\""):
            self.assertNotIn(marker, body)
        self.assertNotIn("window.location.reload();\n          if (refused", body)

    def test_the_page_holds_no_request_and_asks_nothing_on_a_timer(self):
        body = self.body()
        for gone in ("setInterval", "WebSocket", "EventSource", "ws/live", "wss:", "api/wait",
                     "data-wait-url", "data-live", "totoLive", "toto:folder", "onFolderEvent",
                     "watchFolder", "row.v ", "cursor=", "AbortController"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, body)

    def test_the_listing_is_asked_again_on_a_return_to_the_tab_and_after_ones_own_change(self):
        body = self.body()
        self.assertIn("const LISTING_GAP_MS = 30000;", body)
        for event in ("'visibilitychange'", "'focus'", "'pageshow'"):
            self.assertIn(f"addEventListener({event}", body)
        refresh = body[body.index("async refreshListing(force) {"):body.index("takeListing(items) {")]
        self.assertIn("Date.now() - this._listingAsked < LISTING_GAP_MS", refresh)
        self.assertEqual(refresh.count("await fetch("), 1)
        for word in ("setTimeout", "setInterval", "location.reload"):
            self.assertNotIn(word, refresh)
        self.assertNotRegex(refresh, r"\bwhile\s*\(")
        self.assertIn("attempt < 2", refresh)       # asked at most twice, never in a loop
        # One's own move, trash, bulk action and rename ask at once; an
        # upload that landed draws its row from the row door.
        self.assertEqual(body.count("this.refreshListing(true);"), 4)
        self.assertEqual(body.count("this.refreshListing(false);"), 1)
        self.assertIn("onDone: fileId => { if (fileId) this.fetchRow(fileId); }", body)
