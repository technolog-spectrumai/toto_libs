"""The forum as an offline website in a ZIP.

The load-bearing claim is that the archive **works with no network and no
server**: every link inside it resolves to another member of the same archive,
and nothing points at this platform. That is asserted directly — by walking
every `href` and `src` in every page and checking each one is in the ZIP —
rather than by trusting the templates to have stayed standalone.
"""

from __future__ import annotations

import io
import json
import re
import tempfile
import zipfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.forum import export
from toto.forum.models import ForumChannel, ForumMember, ForumMessage

User = get_user_model()

_ROOT = tempfile.mkdtemp()

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)


@override_settings(FORUM_ATTACHMENT_ROOT=_ROOT, MEDIA_ROOT=_ROOT)
# The forum-LEVEL Cleanup and Export desks were removed on 2026-08-29 — both
# operations are per-room now, on each room's Settings tab — so the classes
# that drove `/forum/cleanup/` and `/forum/export/` went with them. Their
# coverage did not: `tests/test_room_hygiene.py` asserts the staff gate, the
# confirmation word, the boundary re-derivation and the archive scoping
# against the room endpoints that replaced them.


class ExportBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform
        from toto.people.models import Person

        Platform.objects.get_or_create(
            site_name="Toto", defaults={"author": "Test",
                                        "publication_year": 2026})
        cls.staff = User.objects.create_user(username="s", password="x",
                                             is_staff=True)
        cls.member = User.objects.create_user(username="m", password="x")
        cls.person = Person.objects.create(user=cls.member, display_name="M")
        cls.room = ForumChannel.objects.create(name="Alpha", slug="alpha")
        cls.other = ForumChannel.objects.create(name="Beta", slug="beta")
        ForumMember.objects.create(channel=cls.room, person=cls.person,
                                   is_active=True)

    def _say(self, body="hello", *, channel=None, days_ago=0, attach=None,
             kind="chat_message", deleted=False, reply_to=None):
        from toto.forum import store

        extra = {}
        if attach:
            extra = {"attachment": ContentFile(attach[1], name=attach[0]),
                     "attachment_name": attach[0],
                     "attachment_mime": "image/png",
                     "attachment_size": len(attach[1])}
        row = store.store_message(channel or self.room, msg_type=kind,
                                  body=body, sender=self.member,
                                  sender_name="M", reply_to=reply_to, **extra)
        if days_ago:
            ForumMessage.objects.filter(pk=row.pk).update(
                created_at=timezone.now() - timedelta(days=days_ago))
        if deleted:
            ForumMessage.objects.filter(pk=row.pk).update(
                deleted_at=timezone.now())
        row.refresh_from_db()
        return row

    def _archive(self):
        plan = export.survey(actor="s")
        data = b"".join(export.stream_archive(plan))
        return zipfile.ZipFile(io.BytesIO(data)), plan

    def _text(self, zf, name):
        return zf.read(name).decode()


class ShapeTests(ExportBase):
    def test_one_page_per_room_and_an_index_linking_to_each(self):
        self._say("in alpha")
        self._say("in beta", channel=self.other)
        zf, _plan = self._archive()
        names = zf.namelist()
        self.assertIn("index.html", names)
        self.assertIn("rooms/alpha.html", names)
        self.assertIn("rooms/beta.html", names)
        index = self._text(zf, "index.html")
        self.assertIn('href="rooms/alpha.html"', index)
        self.assertIn('href="rooms/beta.html"', index)

    def test_messages_are_in_daily_sections_in_order(self):
        self._say("older", days_ago=2)
        self._say("newer")
        page = self._text(self._archive()[0], "rooms/alpha.html")
        days = re.findall(r'id="d-(\d{4}-\d{2}-\d{2})"', page)
        self.assertEqual(days, sorted(days))
        self.assertEqual(len(days), 2)
        self.assertLess(page.index("older"), page.index("newer"))

    def test_an_image_is_in_the_archive_and_referenced_relatively(self):
        self._say("look", attach=("shot.png", PNG), kind="image_message")
        zf, plan = self._archive()
        member = next(m for m in zf.namelist()
                      if m.startswith("attachments/"))
        self.assertEqual(zf.read(member), PNG)
        page = self._text(zf, "rooms/alpha.html")
        self.assertIn(f'src="../{member}"', page)

    def test_a_voice_note_gets_an_audio_element(self):
        self._say("", attach=("voice.webm", b"OggS"), kind="voice_message")
        page = self._text(self._archive()[0], "rooms/alpha.html")
        self.assertIn("<audio", page)

    def test_the_manifest_is_last_and_describes_the_archive(self):
        self._say("hi")
        zf, _plan = self._archive()
        self.assertEqual(zf.namelist()[-1], "manifest.json")
        data = json.loads(self._text(zf, "manifest.json"))
        self.assertEqual(data["format"], export.FORMAT_VERSION)
        self.assertEqual(data["scope"], "all-rooms-operator-export")
        self.assertEqual(data["messages"], 1)
        self.assertIn("language", data)


class OfflineTests(ExportBase):
    """The claim the whole feature rests on."""

    def test_no_page_points_at_this_platform(self):
        self._say("look", attach=("shot.png", PNG), kind="image_message")
        zf, _plan = self._archive()
        for name in [n for n in zf.namelist() if n.endswith(".html")]:
            page = self._text(zf, name)
            for forbidden in ("http://", "https://", "/static/", "/media/",
                              "{% ", "<script"):
                self.assertNotIn(forbidden, page, f"{name} contains {forbidden}")

    def test_every_link_resolves_to_a_member_of_the_archive(self):
        """Walk every href and src. A relative link that resolves to nothing
        is exactly how an archive looks fine and is broken."""
        import posixpath

        self._say("look", attach=("shot.png", PNG), kind="image_message")
        self._say("plain", channel=self.other)
        zf, _plan = self._archive()
        names = set(zf.namelist())
        checked = 0
        for page_name in [n for n in names if n.endswith(".html")]:
            page = self._text(zf, page_name)
            base = posixpath.dirname(page_name)
            for target in re.findall(r'(?:href|src)="([^"]+)"', page):
                if target.startswith("#"):
                    continue
                resolved = posixpath.normpath(posixpath.join(base, target))
                self.assertIn(resolved, names,
                              f"{page_name} links to {target}")
                checked += 1
        self.assertGreater(checked, 0)

    def test_a_reply_anchor_only_points_inside_its_own_page(self):
        parent = self._say("first")
        self._say("second", reply_to=parent)
        page = self._text(self._archive()[0], "rooms/alpha.html")
        self.assertIn(f'href="#m-{parent.id}"', page)
        self.assertIn(f'id="m-{parent.id}"', page)


class ContentTests(ExportBase):
    def test_a_deleted_message_is_a_tombstone_with_no_body_or_file(self):
        """An export must not be the one surface that undoes a deletion, and a
        silent gap is indistinguishable from a message never sent."""
        self._say("regrettable words", attach=("secret.png", PNG),
                  kind="image_message", deleted=True)
        zf, plan = self._archive()
        page = self._text(zf, "rooms/alpha.html")
        self.assertIn("This message was deleted", page)
        self.assertNotIn("regrettable words", page)
        self.assertFalse([n for n in zf.namelist()
                          if n.startswith("attachments/")])

    def test_a_message_body_is_escaped(self):
        self._say('<script>alert("x")</script>')
        page = self._text(self._archive()[0], "rooms/alpha.html")
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_line_breaks_survive_as_breaks_not_as_markup(self):
        self._say("one\ntwo")
        page = self._text(self._archive()[0], "rooms/alpha.html")
        self.assertIn("one<br>two", page)

    def test_the_index_says_the_archive_crosses_every_room(self):
        self._say("hi")
        index = self._text(self._archive()[0], "index.html")
        self.assertIn("including private ones", index)
        self.assertIn("not a live view and not a backup", index)

    def test_an_empty_room_still_gets_a_page(self):
        zf, _plan = self._archive()
        self.assertIn("rooms/beta.html", zf.namelist())
        self.assertIn("Nothing was ever said here",
                      self._text(zf, "rooms/beta.html"))


class DeterminismTests(ExportBase):
    def test_two_exports_of_unchanged_data_match_except_the_manifest(self):
        self._say("hi", attach=("a.png", PNG), kind="image_message")
        first, _p1 = self._archive()
        second, _p2 = self._archive()
        self.assertEqual(first.namelist(), second.namelist())
        for name in first.namelist():
            if name == "manifest.json":
                continue
            self.assertEqual(first.read(name), second.read(name), name)

    def test_the_same_file_posted_twice_is_one_member(self):
        """Content addressing, so a picture shared in four rooms is stored
        once rather than four times."""
        self._say("here", attach=("a.png", PNG), kind="image_message")
        self._say("also here", channel=self.other,
                  attach=("a.png", PNG), kind="image_message")
        zf, _plan = self._archive()
        self.assertEqual(
            len([n for n in zf.namelist() if n.startswith("attachments/")]), 1)

    def test_no_member_can_escape_the_archive_root(self):
        self._say("hi", attach=("../../etc/passwd", PNG), kind="image_message")
        zf, _plan = self._archive()
        import posixpath

        for name in zf.namelist():
            self.assertFalse(export._unsafe(name), name)
            # The invariant is that unzipping cannot write outside the root —
            # not that the string has no dots. The sanitiser turns "/" into
            # "_", so "../../etc/passwd" becomes the harmless FILENAME
            # ".._.._etc_passwd" inside attachments/.
            self.assertFalse(posixpath.normpath(name).startswith(("..", "/")),
                             name)
            # At most one directory deep: index.html and manifest.json sit
            # at the root, rooms/ and attachments/ one level down.
            self.assertLessEqual(len(name.split("/")), 2, name)


class ResilienceTests(ExportBase):
    def test_a_vanished_file_is_named_not_fatal(self):
        row = self._say("look", attach=("gone.png", PNG), kind="image_message")
        storage = ForumMessage._meta.get_field("attachment").storage
        storage.delete(row.attachment.name)
        zf, plan = self._archive()
        data = json.loads(self._text(zf, "manifest.json"))
        self.assertEqual(len(data["skipped"]), 1)
        self.assertEqual(data["skipped"][0]["why"], "missing")
        self.assertIn("rooms/alpha.html", zf.namelist())

    def test_an_oversized_file_is_skipped_rather_than_refusing_everything(self):
        """ireneo's per-member limit guards OPENING an untrusted bundle.
        Reusing it as a refusal here would let one legacy row make a forum
        permanently un-exportable."""
        row = self._say("big", attach=("big.png", PNG), kind="image_message")
        with self.settings():
            original = export.MAX_BLOB_BYTES
            export.MAX_BLOB_BYTES = 1
            try:
                zf, plan = self._archive()
            finally:
                export.MAX_BLOB_BYTES = original
        data = json.loads(self._text(zf, "manifest.json"))
        self.assertEqual(data["skipped"][0]["why"], "too-large")
        self.assertIn("rooms/alpha.html", zf.namelist())
        self.assertEqual(str(row.id), data["skipped"][0]["message"])

    def test_a_cap_refuses_the_whole_export_rather_than_truncating(self):
        """A truncated archive looks complete to whoever holds it."""
        self._say("one")
        self._say("two")
        original = export.MAX_MESSAGES_PER_ROOM
        export.MAX_MESSAGES_PER_ROOM = 1
        try:
            with self.assertRaises(export.ExportTooLarge):
                export.survey()
        finally:
            export.MAX_MESSAGES_PER_ROOM = original

    def test_two_rooms_whose_slugs_differ_only_in_case_get_two_files(self):
        ForumChannel.objects.create(name="Gamma", slug="Alpha2")
        ForumChannel.objects.create(name="gamma", slug="alpha2")
        zf, _plan = self._archive()
        rooms = [n for n in zf.namelist() if n.startswith("rooms/")]
        self.assertEqual(len(rooms), len(set(n.lower() for n in rooms)))

    def test_a_windows_device_name_is_not_used_as_a_filename(self):
        ForumChannel.objects.create(name="Auxiliary", slug="aux")
        zf, _plan = self._archive()
        self.assertIn("rooms/aux-room.html", zf.namelist())


class ReservedSlugTests(ExportBase):
    def test_a_room_cannot_be_called_export(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            ForumChannel(name="Export", slug="export").full_clean()
