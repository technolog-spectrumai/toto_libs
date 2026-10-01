"""What a ``.tpy`` notebook's kernel is handed (2026-10-01): the sibling
files of its bucket that its member may read — ``access.may_read`` as a
queryset — never another member's private file and never the trash. It
was handed every file of the bucket, and code in the notebook can open any
path it is given. RequestFactory, not the url: a host may mount mandragora
at no url and still carry the views.

    manage.py test toto.mandragora.tests_kernel_files
"""

from __future__ import annotations

import json
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

from toto.mandragora import tpy_format, tpy_views
from toto.vault.models import Bucket, VaultDirectory, VaultFile

User = get_user_model()


class KernelFilesTests(TestCase):
    def setUp(self):
        override = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        override.enable()
        self.addCleanup(override.disable)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.carol = User.objects.create_user("carol", password="pass")
        # Bob's bucket; Alice works in it through a folder he shares with her.
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.bob)
        self.shared = VaultDirectory.objects.create(name="shared", bucket=self.bucket,
                                                    owner=self.bob)
        self.shared.allowed_users.add(self.alice)
        self.notebook = self._file(self.alice, "nb.xml", file_type="xml",
                                   body=tpy_format.dumps(tpy_format.new_notebook()).encode())
        self._file(self.alice, "alice.csv")
        self._file(self.carol, "carol-private.csv")
        self._file(self.carol, "carol-public.csv", is_public=True)
        self._file(self.carol, "carol-notes.txt", directory=self.shared)
        self._file(self.alice, "alice-trashed.csv").trash(self.alice)
        kernel = mock.patch.object(tpy_views, "client")
        self.kernel = kernel.start()
        self.addCleanup(kernel.stop)
        self.kernel.start.return_value = {"state": "started"}

    def _file(self, owner, title, *, file_type="text", body=b"a,b\n1,2\n", **extra):
        return VaultFile.objects.create(
            owner=owner, title=title, file_type=file_type, bucket=self.bucket,
            file=SimpleUploadedFile(title, body), **extra)

    def _handed(self, user, notebook=None):
        """The names the kernel was started with, for ``user``'s notebook."""
        request = RequestFactory().post("/x/", data=json.dumps({}),
                                        content_type="application/json")
        request.user = user
        response = tpy_views.tpy_start_kernel(request, (notebook or self.notebook).pk)
        self.assertEqual(response.status_code, 200)
        (_session, config), _kwargs = self.kernel.start.call_args
        return config.get("vault_files", {})

    def test_the_kernel_gets_only_what_its_member_may_read(self):
        handed = self._handed(self.alice)
        # Her own, the public one, and the shared folder's; not Carol's
        # private file, not the trash, not the notebook itself.
        self.assertEqual(set(handed), {"alice.csv", "carol-public.csv", "carol-notes.txt"})

    def test_no_unreadable_path_reaches_the_kernel(self):
        private = VaultFile.objects.get(title="carol-private.csv")
        trashed = VaultFile.all_objects.get(title="alice-trashed.csv")
        paths = set(self._handed(self.alice).values())
        self.assertNotIn(private.file.path, paths)
        self.assertNotIn(trashed.file.path, paths)

    def test_the_bucket_s_owner_gets_every_live_file(self):
        notebook = self._file(self.bob, "bob.xml", file_type="xml",
                              body=tpy_format.dumps(tpy_format.new_notebook()).encode())
        self.assertEqual(set(self._handed(self.bob, notebook)),
                         {"nb.xml", "alice.csv", "carol-private.csv", "carol-public.csv",
                          "carol-notes.txt"})

    def test_a_kept_bucket_opens_to_its_clearance_alone(self):
        from toto.people.models import Person
        from toto.socialhub.models import Clearance
        from toto.vault.models import BucketClearance

        lab = Clearance.objects.create(name="lab", slug="lab")
        BucketClearance.objects.create(bucket=self.bucket, clearance=lab)
        Person.objects.create(user=self.alice, display_name="Alice").clearances.add(lab)
        # The clearance decides alone in a kept bucket: its holder reads every
        # live file in it, the public flag and the folder no longer matter.
        self.assertEqual(set(self._handed(self.alice)),
                         {"alice.csv", "carol-private.csv", "carol-public.csv",
                          "carol-notes.txt"})
