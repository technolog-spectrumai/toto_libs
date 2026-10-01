"""What ``erase_user`` removes beyond the cascade (2026-10-01, 37c.21;
``toto.core.erasure``).

The privacy notice's survey found what an erased member left behind: the
avatar's file, the bodies of their files' saved versions, their home pin,
their admitted application and its references, their name and picture on
forum messages, the pictures and voice recordings they sent, and their
username in the names of their personal bucket and prepaid ledger account.
Each test here fails on the erase as it was.

    manage.py test toto.core.tests_erase_leftovers
"""

import io
import json
import os
import tempfile
import unittest

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.core.management.commands.erase_user import plan
from toto.locations.models import Address
from toto.people.models import LocationSharing, Person
from toto.socialhub.models import Community, MembershipApplication, ReferenceRequest

User = get_user_model()
MEDIA = tempfile.mkdtemp(prefix="erase-leftovers-")
FORUM = tempfile.mkdtemp(prefix="erase-leftovers-forum-")
VAULT = dict(FORUM_VAULT_PASSWORD="forum-test-vault-passphrase",
             FORUM_PASSWORD_KDF={"memory_cost": 8, "iterations": 1, "lanes": 1},
             FORUM_ALLOW_LOCAL_KEY_STORE=True)


def erase(case, username):
    out = io.StringIO()
    with case.captureOnCommitCallbacks(execute=True):
        call_command("erase_user", username, "--confirm", username, stdout=out)
    return json.loads(out.getvalue().strip().splitlines()[-1])


@override_settings(MEDIA_ROOT=MEDIA, FORUM_ATTACHMENT_ROOT=FORUM, **VAULT)
class LeftoverCase(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser("root", "root@example.com", "pw")
        self.ada = User.objects.create_user("ada", "ada@example.com", "pw")
        self.person = Person.objects.create(user=self.ada, display_name="Ada Lovelace",
                                            email="ada@example.com")
        self.bob = User.objects.create_user("bob", "bob@example.com", "pw")
        self.bob_person = Person.objects.create(user=self.bob, display_name="Bob")


class AvatarTests(LeftoverCase):
    def test_the_avatar_file_goes(self):
        self.person.avatar.save("ada.png", ContentFile(b"\x89PNG not really"), save=True)
        path = self.person.avatar.path
        self.assertTrue(os.path.exists(path))
        erase(self, "ada")
        self.assertFalse(os.path.exists(path))


class VersionBodyTests(LeftoverCase):
    def file(self, owner, text):
        from toto.vault.models import Bucket, VaultFile

        bucket, _ = Bucket.objects.get_or_create(slug=f"b-{owner.username}", defaults={
            "name": f"B {owner.username}", "owner": owner})
        f = VaultFile(owner=owner, title=f"{text}.txt", key=f"{owner.username}-{text}",
                      file_type="text", bucket=bucket)
        f.file.save(f"{text}.txt", ContentFile(text.encode()), save=True)
        return f

    def test_the_bodies_only_their_files_cite_go_and_a_shared_one_stays(self):
        from toto.vault import versions
        from toto.vault.models import VersionBlob

        mine = self.file(self.ada, "draft")
        versions.save_version(mine, body=b"only ada wrote this", author=self.ada, label="v1")
        versions.save_version(mine, body=b"said by both", author=self.ada, label="v2")
        theirs = self.file(self.bob, "other")
        versions.save_version(theirs, body=b"said by both", author=self.bob, label="b1")
        own = VersionBlob.objects.get(content_hash=versions._digest(b"only ada wrote this"))
        shared = VersionBlob.objects.get(content_hash=versions._digest(b"said by both"))
        own_path = own.data.path
        report = plan(self.ada)
        self.assertEqual(report["beyond"]["version_bodies"], 1)
        erase(self, "ada")
        self.assertFalse(VersionBlob.objects.filter(pk=own.pk).exists())
        self.assertFalse(os.path.exists(own_path))
        self.assertTrue(VersionBlob.objects.filter(pk=shared.pk).exists())


class ApplicationTests(LeftoverCase):
    def test_the_admitted_application_and_its_references_go(self):
        guild = Community.objects.create(name="Cedar Guild", slug="cedar")
        application = MembershipApplication.objects.create(
            email="Ada@Example.com", community=guild, code="424242",
            expires_at=timezone.now() + timezone.timedelta(days=7))
        ReferenceRequest.objects.create(application=application, referrer=self.bob_person,
                                        message="I know her", status="accepted")
        other = MembershipApplication.objects.create(
            email="someone@example.com", community=guild, code="515151",
            expires_at=timezone.now() + timezone.timedelta(days=7))
        report = plan(self.ada)
        self.assertEqual((report["beyond"]["applications"], report["beyond"]["references"]),
                         (1, 1))
        self.assertTrue(MembershipApplication.objects.filter(pk=application.pk).exists())
        erase(self, "ada")
        self.assertFalse(MembershipApplication.objects.filter(pk=application.pk).exists())
        self.assertFalse(ReferenceRequest.objects.filter(message="I know her").exists())
        self.assertTrue(MembershipApplication.objects.filter(pk=other.pk).exists())


class AddressTests(LeftoverCase):
    def test_the_home_pin_and_their_unused_addresses_go(self):
        home = Address.objects.create(street="Hidden Lane", latitude=52.0, longitude=21.0)
        Person.objects.filter(pk=self.person.pk).update(address=home,
                                                        location_sharing=LocationSharing.EXACT)
        added = Address.objects.create(street="Their note", created_by=self.ada)
        erase(self, "ada")
        self.assertFalse(Address.objects.filter(pk__in=[home.pk, added.pk]).exists())

    def test_an_address_others_use_stays(self):
        from toto.events.models import ScheduledEvent

        used = Address.objects.create(street="Town Hall", created_by=self.ada)
        start = timezone.now() + timezone.timedelta(days=1)
        ScheduledEvent.objects.create(title="Meeting", start_time=start,
                                      end_time=start + timezone.timedelta(hours=1), address=used)
        shared_home = Address.objects.create(street="Family Home")
        Person.objects.filter(pk=self.person.pk).update(address=shared_home)
        Person.objects.filter(pk=self.bob_person.pk).update(address=shared_home)
        erase(self, "ada")
        self.assertTrue(Address.objects.filter(pk=used.pk, created_by=None).exists())
        self.assertTrue(Address.objects.filter(pk=shared_home.pk).exists())


class NameTests(LeftoverCase):
    def test_the_personal_bucket_loses_the_username(self):
        from toto.vault.models import Bucket, personal_bucket

        bucket = personal_bucket(self.ada)
        self.assertEqual((bucket.name, bucket.slug), ("Personal — ada", "personal-ada"))
        erase(self, "ada")
        bucket.refresh_from_db()
        self.assertIsNone(bucket.owner_id)
        self.assertNotIn("ada", bucket.name + bucket.slug)
        self.assertEqual(bucket.name, f"Personal — deleted account {bucket.pk}")
        # A new account with the old name gets a bucket of its own.
        again = User.objects.create_user("ada", "new-ada@example.com", "pw")
        self.assertNotEqual(personal_bucket(again).pk, bucket.pk)
        self.assertEqual(Bucket.objects.filter(slug="personal-ada").count(), 1)

    @unittest.skipUnless(apps.is_installed("toto.assets"), "no ledger on this host")
    def test_the_prepaid_ledger_account_loses_the_username(self):
        from toto.assets.prepaid import GONE_HOLDER_NAME, get_or_create_prepaid_account

        account, _ = get_or_create_prepaid_account(self.ada)
        self.assertEqual(account.name, "Prepaid — ada")
        erase(self, "ada")
        account.refresh_from_db()
        self.assertEqual((account.name, account.user_id), (GONE_HOLDER_NAME, None))


@unittest.skipUnless(apps.is_installed("toto.forum"), "no forum on this host")
class ForumTests(LeftoverCase):
    def setUp(self):
        from django.core.cache import cache

        from toto.forum import rooms

        super().setUp()
        rooms.vault.clear_cache()
        cache.clear()

    def room(self, slug, *, encrypted=False):
        from toto.forum import rooms
        from toto.forum.models import ForumChannel

        channel = ForumChannel.objects.create(name=slug.title(), slug=slug, created_by=self.bob,
                                              is_encrypted=encrypted)
        key = rooms.create_room_key(channel) if encrypted else None
        return channel, key

    def send(self, channel, key, *, body="", attachment=None, msg_type="chat_message"):
        from toto.forum import store

        return store.store_message(
            channel, msg_type=msg_type, body=body, sender=self.ada, sender_name="Ada Lovelace",
            sender_avatar_url="/media/avatars/ada.png",
            attachment=ContentFile(attachment, name="att.bin") if attachment else None,
            attachment_name="att.bin" if attachment else "",
            attachment_mime="image/png" if attachment else "",
            attachment_size=len(attachment) if attachment else None, key=key)

    def test_the_text_stays_without_the_name_and_the_attachments_go(self):
        from toto.forum.models import ForumMessage

        paths = []
        for slug, encrypted in (("plain", False), ("sealed", True)):
            channel, key = self.room(slug, encrypted=encrypted)
            self.send(channel, key, body="Hello all")
            picture = self.send(channel, key, attachment=b"picture-bytes",
                                msg_type="image_message")
            voice = self.send(channel, key, attachment=b"voice-bytes",
                              msg_type="voice_message")
            captioned = self.send(channel, key, body="Look at this",
                                  attachment=b"captioned-bytes", msg_type="image_message")
            paths += [row.attachment.path for row in (picture, voice, captioned)]
        for path in paths:
            self.assertTrue(os.path.exists(path), path)
        report = plan(self.ada)
        self.assertEqual((report["beyond"]["forum_messages"],
                          report["beyond"]["forum_attachments"]), (8, 6))
        erase(self, "ada")
        rows = ForumMessage.objects.all()
        # The bare picture and the bare recording are gone, row and bytes; the
        # captioned one keeps its caption as an ordinary message.
        self.assertEqual(rows.count(), 4)
        for row in rows:
            self.assertEqual((row.sender_id, row.sender_name, row.sender_avatar_url),
                             (None, "Former member", ""))
            self.assertFalse(row.attachment)
            self.assertEqual(row.msg_type, "chat_message")
        for path in paths:
            self.assertFalse(os.path.exists(path), path)
        self.assertEqual(rows.filter(channel__slug="plain", body="Look at this").count(), 1)

    def test_other_peoples_messages_are_untouched(self):
        from toto.forum import store
        from toto.forum.models import ForumMessage

        channel, _ = self.room("plain")
        store.store_message(channel, msg_type="chat_message", body="Bob here", sender=self.bob,
                            sender_name="Bob", sender_avatar_url="/media/avatars/bob.png")
        self.send(channel, None, body="Ada here")
        erase(self, "ada")
        bob = ForumMessage.objects.get(body="Bob here")
        self.assertEqual((bob.sender_name, bob.sender_avatar_url), ("Bob", "/media/avatars/bob.png"))


class ReportTests(LeftoverCase):
    def test_the_report_names_it_and_changes_nothing(self):
        from toto.vault.models import personal_bucket

        self.person.avatar.save("ada.png", ContentFile(b"x"), save=True)
        personal_bucket(self.ada)
        report = plan(self.ada)
        self.assertEqual(report["beyond"]["avatar_file"], 1)
        self.assertEqual(report["beyond"]["buckets_renamed"], 1)
        notes = " ".join(report["notes"])
        self.assertIn("profile picture's file", notes)
        self.assertIn("deleted account", notes)
        self.assertTrue(os.path.exists(self.person.avatar.path))
        self.assertTrue(User.objects.filter(username="ada").exists())

    def test_the_audit_record_counts_it(self):
        from toto.audit.models import AuditRecord

        self.person.avatar.save("ada.png", ContentFile(b"x"), save=True)
        erase(self, "ada")
        record = AuditRecord.objects.get(action="AUTH.ACCOUNT_ERASED")
        self.assertEqual(record.metadata["beyond"]["avatar_file"], 1)
