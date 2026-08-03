"""Runnable settings for the memo suite.

Same reasoning as ``toto.primula.testing.settings``: no host has *only* memo, and
until this module existed memo's 500 lines of tests were run by no gate at all.
GIS is off via the usual ``HAS_GIS`` + ``MIGRATION_MODULES`` pair so this runs on
any interpreter with no GDAL. ``toto.editor`` is installed on purpose: the plugin
tests prove that a deck routes to memo *while a plain .xml file still routes to
the generic ACE editor*, and that comparison only exists when both registries are
populated.
"""
import tempfile
from pathlib import Path

from toto.registry import BASE_APPS

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "memo-suite-not-a-secret"
DEBUG = False
ALLOWED_HOSTS = ["*"]

# locations loads without geometry; see the module docstring.
HAS_GIS = False

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "django_jsonform",
    "django_json_widget",
    "rest_framework",
    "colorfield",
    "reversion",
    "markdownx",
    "trix_editor",
    # BASE_APPS rather than CORE_APPS: oya/header.html — which every memo page
    # inherits — reverses `sso:login`/`sso:logout`, so the view tests need the auth
    # block to render pages at all.
    *BASE_APPS,
    "toto.editor",
    "toto.memo",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "toto.memo.testing.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

MIGRATION_MODULES = {"locations": "toto.locations.migrations_nogis"}

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}

STATIC_URL = "static/"
# A temp dir, NOT `BASE_DIR / "staticfiles"`. BASE_DIR is inside packages/, and
# `staticfiles/` is a gitignored directory name that matches at any depth — so a
# collectstatic under these settings would drop files that
# test_no_source_file_under_packages_is_gitignored then reports as offenders.
# memo is the first app in this package to ship static files, so it is the first
# that can actually trip it.
STATIC_ROOT = Path(tempfile.mkdtemp(prefix="memo-suite-static-"))
# Deck bytes are real files: write them somewhere disposable, never the repo tree.
MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="memo-suite-media-"))

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("pl", "Polski")]
USE_I18N = True
USE_TZ = True

FIELD_ENCRYPTION_KEY = "zqx3Wt0nqTfKqBPXCsFtHQOMoO0v8kBn8ZQmFqBLLwo="
PLATFORM_DOMAIN = "memo.test"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"handlers": ["null"], "level": "CRITICAL"},
    "handlers": {"null": {"class": "logging.NullHandler"}},
}
