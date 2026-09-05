"""Attaching a vault file must never widen who can read it."""

from __future__ import annotations

import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.http import Http404
from django.test import TestCase, override_settings

from toto.vault import attach
from toto.vault.models import Bucket, VaultDirectory, VaultFile

MEDIA = tempfile.mkdtemp(prefix="vault-attach-")


@override_settings(MEDIA_ROOT=MEDIA)
class AttachTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("attach-owner", password="x")
        self.other = User.objects.create_user("attach-other", password="x")
        self.bucket = Bucket.objects.create(name=f"b-{self.id()}", owner=self.owner)

    def _file(self, *, owner=None, public=False, bucket=None, directory=None,
              title="note.txt"):
        vault_file = VaultFile(
            owner=owner or self.owner, title=title, file_type="text",
            is_public=public, bucket=bucket, directory=directory,
        )
        vault_file.file.save(title, ContentFile(b"hello"), save=False)
        vault_file.save()
        return vault_file


class ValidateReferenceTests(AttachTestCase):
    def test_the_owner_may_attach_their_own_file(self):
        mine = self._file()
        self.assertEqual(attach.validate_reference(self.owner, mine.pk), mine)

    def test_a_stranger_may_not_attach_a_private_file(self):
        mine = self._file()
        with self.assertRaises(Http404):
            attach.validate_reference(self.other, mine.pk)

    def test_a_stranger_may_not_attach_a_PUBLIC_file(self):
        """The clause that makes this stricter than `may_read`, on purpose.

        A public file is readable by everybody, so `may_read` says yes. But
        attaching copies a reference into a record with its own audience, and
        "I can see it" is a weaker claim than "it is mine to put somewhere".
        Someone else's public file is not theirs to hand around.
        """
        theirs = self._file(public=True)
        self.assertTrue(theirs.is_public)
        with self.assertRaises(Http404):
            attach.validate_reference(self.other, theirs.pk)

    def test_a_bucket_owner_may_attach_a_file_in_their_bucket(self):
        theirs = self._file(owner=self.other, bucket=self.bucket)
        self.assertEqual(attach.validate_reference(self.owner, theirs.pk), theirs)

    def test_a_directory_member_may_attach(self):
        directory = VaultDirectory.objects.create(
            name="shared", bucket=self.bucket, owner=self.owner)
        directory.allowed_users.add(self.other)
        shared = self._file(directory=directory)
        self.assertEqual(attach.validate_reference(self.other, shared.pk), shared)

    def test_a_missing_pk_is_404_not_500(self):
        for bad in (None, "", 0, "abc", 10_000_000):
            with self.subTest(pk=bad):
                with self.assertRaises(Http404):
                    attach.validate_reference(self.owner, bad)

    def test_the_refusal_does_not_distinguish_absent_from_forbidden(self):
        """404 both ways, so the pk space cannot be probed."""
        mine = self._file()
        forbidden = absent = None
        try:
            attach.validate_reference(self.other, mine.pk)
        except Http404 as exc:
            forbidden = str(exc)
        try:
            attach.validate_reference(self.other, 9_999_999)
        except Http404 as exc:
            absent = str(exc)
        self.assertEqual(forbidden, absent)

    def test_file_types_narrows_without_changing_the_refusal(self):
        mine = self._file()
        self.assertEqual(
            attach.validate_reference(self.owner, mine.pk, file_types=("text",)),
            mine)
        with self.assertRaises(Http404):
            attach.validate_reference(self.owner, mine.pk, file_types=("pdf",))


class ReadableTests(AttachTestCase):
    """Access is re-checked at render, because it can be revoked after attach."""

    def test_an_owner_still_reads_their_attachment(self):
        mine = self._file()
        self.assertTrue(attach.readable(self.owner, mine))

    def test_unsharing_a_directory_hides_an_existing_attachment(self):
        """The reason `readable` exists at all.

        The attachment row recorded a decision made once. Revoking the share
        must take effect without anybody remembering to delete the row.
        """
        directory = VaultDirectory.objects.create(
            name="shared", bucket=self.bucket, owner=self.owner)
        directory.allowed_users.add(self.other)
        shared = self._file(directory=directory)
        self.assertTrue(attach.readable(self.other, shared))

        directory.allowed_users.remove(self.other)
        shared.refresh_from_db()
        self.assertFalse(attach.readable(self.other, shared))

    def test_a_public_file_stays_readable_by_anyone(self):
        """Attach is stricter than read; read is still read."""
        theirs = self._file(public=True)
        self.assertTrue(attach.readable(self.other, theirs))

    def test_none_is_false_not_an_exception(self):
        self.assertFalse(attach.readable(self.owner, None))


class VisibleTests(AttachTestCase):
    def test_it_drops_rows_whose_file_is_no_longer_readable(self):
        class Row:
            def __init__(self, vault_file):
                self.vault_file = vault_file

        mine = self._file(title="mine.txt")
        theirs = self._file(owner=self.other, title="theirs.txt")
        rows = [Row(mine), Row(theirs)]

        kept = attach.visible(self.owner, rows)

        self.assertEqual([row.vault_file for row in kept], [mine])
