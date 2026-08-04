"""Shared setup.

`toto` is a PEP 420 namespace package, so these modules are named explicitly on
the command line — `manage.py test toto.cyprian` discovers nothing. They are
listed one by one in zenobia/scripts/clean_env_test.sh.
"""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.core.models import Platform
from toto.vault.models import Bucket

User = get_user_model()

# Escapes the strict whitenoise manifest storage, which otherwise needs a real
# collectstatic run before a page can render.
storage_override = override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})


@storage_override
class CyprianTestCase(TestCase):
    def setUp(self):
        # A document's bytes are a real file, and the deployed MEDIA_ROOT is a
        # root-owned bind mount. Give each test its own directory.
        self._media = tempfile.mkdtemp(prefix="cyprian-test-")
        self._media_override = override_settings(MEDIA_ROOT=self._media)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(shutil.rmtree, self._media, ignore_errors=True)

        # Required: the base templates 404 without an active Platform, because
        # PageProcessor._get_config raises Http404 when there is none.
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)

        self.owner = User.objects.create_user("writer", password="pw")
        self.other = User.objects.create_user("stranger", password="pw")
        self.bucket = Bucket.objects.create(name="Papers", slug="papers",
                                            owner=self.owner,
                                            storage_backend="local")
