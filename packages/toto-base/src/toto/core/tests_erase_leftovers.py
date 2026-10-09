"""What ``erase_user`` removes beyond the cascade (2026-10-01, 37c.21;
``toto.core.erasure``).

The privacy notice's survey found what an erased member left behind: the
avatar's file, the bodies of their files' saved versions, their home pin,
their admitted application and its references, and their username in the
names of their personal bucket and prepaid ledger account. Each test here
fails on the erase as it was.

It found their name and pictures in the forum too. That branch of the erase
and its tests left on 2026-10-09 with the parked forum (toto-chat's
``toto/forum/PARKED.md`` names the commit that brings both back);
``NoForumBranchTests`` holds that nothing of it is left behind.

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


def erase(case, username):
    out = io.StringIO()
    with case.captureOnCommitCallbacks(execute=True):
        call_command("erase_user", username, "--confirm", username, stdout=out)
    return json.loads(out.getvalue().strip().splitlines()[-1])


@override_settings(MEDIA_ROOT=MEDIA)
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


class NoForumBranchTests(LeftoverCase):
    """The forum is parked (2026-10-09): the erase, its report and the copy
    of a member's data know nothing of one, whatever a host installs."""

    def test_the_report_counts_nothing_of_a_forum(self):
        report = plan(self.ada)
        self.assertTrue(report["beyond"], "no count at all: the check is vacuous")
        self.assertFalse([key for key in report["beyond"] if "forum" in key])
        self.assertFalse([note for note in report["notes"] if "forum" in note.lower()])

    def test_the_erase_keeps_no_forum_step(self):
        import inspect
        from dataclasses import fields

        from toto.core import erasure

        names = [f.name for f in fields(erasure.Leftovers)]
        self.assertIn("avatar", names)
        self.assertFalse([name for name in names if "forum" in name])
        self.assertNotIn("toto.forum", inspect.getsource(erasure))

    def test_the_data_copy_has_no_forum_table(self):
        import inspect

        from toto.core import personal_data

        names = [table.name for table in personal_data.tables_for(self.ada)]
        self.assertIn("account", names)
        self.assertFalse([name for name in names if "forum" in name])
        self.assertNotIn("toto.forum", inspect.getsource(personal_data))


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
