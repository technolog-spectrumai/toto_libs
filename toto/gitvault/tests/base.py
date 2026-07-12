"""Shared fixtures: tmp MEDIA_ROOT + a small vault tree.

Run from portal/ with the app enabled:
    BUILD_GITVAULT=1 python manage.py test toto.gitvault
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

        self.user = User.objects.create_user("alice", "alice@example.com", "pw")
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

    def _file(self, title, content, directory, encrypted=False, key=""):
        return VaultFile.objects.create(
            owner=self.user,
            title=title,
            key=key,  # VaultFile.save() raises on duplicate auto-slugged keys
            bucket=self.bucket,
            directory=directory,
            file=SimpleUploadedFile(title, content),
            is_encrypted=encrypted,
            file_type="text",
        )

    def make_repo(self):
        from toto.gitvault import services
        return services.init_repo(self.root, self.user)
