"""Shared fixtures: tmp MEDIA_ROOT + a small vault tree.

Run from zenobia/ with the app enabled. `toto` is a PEP 420 namespace package,
so the test runner cannot discover it by package label — name the test modules:
    BUILD_GITVAULT=1 python manage.py test toto.gitvault.tests.test_views ...

scripts/clean_env_test.sh runs the whole set.
"""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from toto.vault.models import Bucket, VaultDirectory, VaultFile


class GitvaultTestCase(TestCase):
    """A bucket with:  root/            (repo candidate)
                        ├── notes.txt
                        ├── sub/deep.txt
                        └── secret.txt   (is_encrypted=True → invisible to git)
    """

    def setUp(self):
        self.temp_media = tempfile.mkdtemp(prefix="gitvault-test-media-")
        self._override = override_settings(MEDIA_ROOT=self.temp_media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(shutil.rmtree, self.temp_media, True)

        # Staff, because git is staff-gated by default (GITVAULT_ACCESS) —
        # the functional suites exercise git, not the gate; permission
        # matrices live in test_permissions.
        self.user = User.objects.create_user("alice", "alice@example.com", "pw",
                                             is_staff=True)
        self.bucket = Bucket.objects.create(name="Main", slug="main", owner=self.user)
        self.root = VaultDirectory.objects.create(
            bucket=self.bucket, owner=self.user, name="project"
        )
        self.sub = VaultDirectory.objects.create(
            bucket=self.bucket, owner=self.user, name="sub", parent=self.root
        )
        self.f_notes = self._file("notes.txt", b"hello notes\n", self.root)
        self.f_deep = self._file("deep.txt", b"deep content\n", self.sub)
        self.f_secret = self._file("secret.txt", b"ciphertext", self.root, encrypted=True)

    def _file(self, title, content, directory, encrypted=False, key="",
              file_type="text"):
        # file_type is a parameter because the export engine reads it: a
        # rendition (report.pdf beside report.xml) is recognised by the pair of
        # types, not by the extension alone.
        return VaultFile.objects.create(
            owner=self.user,
            title=title,
            key=key,  # VaultFile.save() raises on duplicate auto-slugged keys
            bucket=self.bucket,
            directory=directory,
            file=SimpleUploadedFile(title, content),
            is_encrypted=encrypted,
            file_type=file_type,
        )

    def make_repo(self):
        from toto.gitvault import services
        return services.init_repo(self.root, self.user)
