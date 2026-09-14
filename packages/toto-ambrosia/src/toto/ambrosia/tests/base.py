"""Shared setup.

`toto` is a PEP 420 namespace package, so these modules are named explicitly on
the command line — `manage.py test toto.ambrosia` discovers nothing. They are
listed one by one in zenobia/scripts/clean_env_test.sh.
"""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.utils.text import slugify
from django.test import TestCase, override_settings

from toto.core.models import Platform

from toto.ambrosia import services

from . import testlab

User = get_user_model()


# Escapes the strict whitenoise manifest storage, which otherwise needs a real
# collectstatic run before a page can render.
storage_override = override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})


@storage_override
class AmbrosiaTestCase(TestCase):
    def setUp(self):
        # Every workspace writes real files, and the deployed MEDIA_ROOT is a
        # root-owned bind mount. Give each test its own directory and take it
        # away afterwards — the same shape as toto.repo's test base.
        self._media = tempfile.mkdtemp(prefix="ambrosia-test-")
        self._media_override = override_settings(MEDIA_ROOT=self._media)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(shutil.rmtree, self._media, ignore_errors=True)

        # Required: the base templates 404 without an active Platform, because
        # PageProcessor._get_config raises Http404 when there is none.
        Platform.objects.create(
            site_name="Test Platform", author="Tests",
            publication_year=2026, active=True,
        )
        # Staff, because execution is gated on it by default — see permissions.
        self.owner = User.objects.create_user(
            "dev", password="pw", is_staff=True)
        self.other = User.objects.create_user("stranger", password="pw")
        self.admin = User.objects.create_superuser(
            "root", email="root@example.com", password="pw")

    def make_bucket(self, *, owner=None, name=None, slug=None):
        """A vault bucket to put workspaces in. Ambrosia no longer makes these."""
        from toto.vault.models import Bucket

        owner = owner or self.owner
        name = name or f"Bucket {Bucket.objects.count() + 1}"
        return Bucket.objects.create(
            name=name, owner=owner, slug=slug or slugify(name),
            storage_backend="local")

    def make_workspace(self, *, owner=None, name="Test workspace", bucket=None,
                       directory=None, new_directory_name=None, **kw):
        """A workspace in its own new folder, in a bucket made on demand."""
        owner = owner or self.owner
        if bucket is None and directory is None:
            bucket = self.make_bucket(owner=owner)
        if bucket is None:
            bucket = directory.bucket
        if directory is None and new_directory_name is None:
            new_directory_name = name
        return services.create_workspace(
            owner=owner, name=name, bucket=bucket, directory=directory,
            new_directory_name=new_directory_name or "", **kw)


@override_settings(ROOT_URLCONF=testlab.URLCONF)
class TestlabTestCase(AmbrosiaTestCase):
    """AmbrosiaTestCase with the test lab registered and its routes mounted.

    For everything reached through a language app's namespace: the lobby, the
    room, the file endpoints, settings, hibernation and the workspace API. See
    tests/testlab.py for why the suite no longer borrows a real lab. A test
    that needs a hook swapped calls `testlab.install(self, run=...)` again.
    """

    def setUp(self):
        super().setUp()
        self.lab = testlab.install(self)
