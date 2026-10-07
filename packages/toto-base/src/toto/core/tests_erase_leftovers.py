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
from toto.people.models import Person
from toto.socialhub.models import Community, MembershipApplication, ReferenceRequest

User = get_user_model()
MEDIA = tempfile.mkdtemp(prefix="erase-leftovers-")
VAULT = dict(FORUM_VAULT_PASSWORD="forum-test-vault-passphrase")


def erase(case, username):
    out = io.StringIO()
    with case.captureOnCommitCallbacks(execute=True):
        call_command("erase_user", username, "--confirm", username, stdout=out)
    return json.loads(out.getvalue().strip().splitlines()[-1])


@override_settings(MEDIA_ROOT=MEDIA, **VAULT)
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
    """The simplified forum (2026-10-07): one sealed channel per community,
    pictures kept by the vault."""

    PNG = b"\x89PNG\r\n\x1a\n" + b"picture-bytes"

    def setUp(self):
        from django.core.cache import cache

        from toto.forum import channels, keys

        super().setUp()
        keys.forget()
        keys.vault.clear_cache()
        cache.clear()
        self.addCleanup(keys.forget)
        self.addCleanup(keys.vault.clear_cache)
        self.channel = channels.ensure_channel(Community.objects.create(name="Guild"))

    def send(self, user, *, text="", picture=None):
        import uuid

        from toto.forum import posting

        message, _replay = posting.post_message(
            user, self.channel, text=text, op=str(uuid.uuid4()),
            image=(picture, "image/png") if picture else None)
        return message

    def test_the_text_stays_without_the_name_and_the_pictures_go(self):
        from toto.forum import keys, sealing
        from toto.forum.models import ForumMessage
        from toto.vault.models import VaultFile

        self.send(self.ada, text="Hello all")
        bare = self.send(self.ada, picture=self.PNG)
        captioned = self.send(self.ada, text="Look at this", picture=self.PNG)
        paths = [row.attachment.file.path for row in (bare, captioned)]
        for path in paths:
            self.assertTrue(os.path.exists(path), path)
        report = plan(self.ada)
        self.assertEqual((report["beyond"]["forum_messages"],
                          report["beyond"]["forum_attachments"]), (3, 2))
        erase(self, "ada")
        # The bare picture is a tombstone; the captioned one keeps its
        # caption as an ordinary message; the bytes are gone from the vault.
        self.assertIsNotNone(ForumMessage.objects.get(pk=bare.pk).removed_at)
        rows = ForumMessage.objects.filter(removed_at__isnull=True)
        self.assertEqual(rows.count(), 2)
        key = keys.open_key(self.channel)
        texts = set()
        for row in rows:
            self.assertEqual((row.sender_id, row.sender_name, row.kind),
                             (None, "Former member", "text"))
            self.assertIsNone(row.attachment_id)
            texts.add(sealing.open_text(key, row.body_sealed, channel_id=row.channel_id,
                                        message_id=row.id))
        self.assertEqual(texts, {"Hello all", "Look at this"})
        self.assertEqual(VaultFile.all_objects.filter(bucket=self.channel.bucket).count(), 0)
        for path in paths:
            self.assertFalse(os.path.exists(path), path)

    def test_other_peoples_messages_are_untouched(self):
        from toto.forum.models import ForumMessage

        theirs = self.send(self.bob, text="Bob here", picture=self.PNG)
        self.send(self.ada, text="Ada here")
        erase(self, "ada")
        row = ForumMessage.objects.get(pk=theirs.pk)
        self.assertEqual(row.sender_id, self.bob.pk)
        self.assertIsNotNone(row.attachment_id)
        self.assertNotEqual(row.sender_name, "Former member")


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
