"""Runnable settings for the kanban suite.

Same reasoning as ``toto.memo.testing.settings``: no host has *only* kanban, and
until this module existed kanban's 190-odd tests were run by no gate anywhere in
the monorepo — the app whose schema three hosts share was the one nothing
exercised. GIS is off via the usual ``HAS_GIS`` + ``MIGRATION_MODULES`` pair so
this runs on any interpreter with no GDAL; the zone-containment tests already
skip themselves when it is.

``toto.cyprian`` is installed on purpose. The wiki's whole point is that its
pages are written in the writer, and the bridge tests are the ones that prove a
project member can open a document they do not own — which only means anything
when both apps are present. ``toto.memo`` comes with it: cyprian's own checks
turn its absence into a ``manage.py check`` error.
"""
import tempfile
from pathlib import Path

from toto.registry import BASE_APPS

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "kanban-suite-not-a-secret"
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
    "toto.cyprian",
    # Not optional: cyprian.E003 is a hard system check, because the writer's
    # sanitisers live here since 8/2026 and unsanitised rich text stored once is
    # stored forever. Django refuses to start without it, so leaving it out did
    # not degrade these tests — it stopped them running at all.
    "toto.antivirus",
    # antivirus.E001 in turn: the job runner has been compulsory since 8/2026,
    # and a settings file that installs antivirus without it is hand-broken
    # rather than configured. Same one-line chain the real hosts resolve
    # through toto.features.
    # mandragora before workflows: workflows.0001_initial depends on
    # mandragora.0001_initial, so without it the migration graph itself will
    # not build.
    "toto.mandragora",
    "toto.workflows",
    "toto.kanban",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "toto.kanban.testing.urls"

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
# memo is the first app in this package to ship static files; kanban inherits
# the same hazard through cyprian\'s.
STATIC_ROOT = Path(tempfile.mkdtemp(prefix="kanban-suite-static-"))
# A wiki page\'s prose is a real file in the vault: somewhere disposable, never
# the repo tree.
MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="kanban-suite-media-"))

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("pl", "Polski")]
USE_I18N = True
USE_TZ = True

FIELD_ENCRYPTION_KEY = "zqx3Wt0nqTfKqBPXCsFtHQOMoO0v8kBn8ZQmFqBLLwo="
PLATFORM_DOMAIN = "kanban.test"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"handlers": ["null"], "level": "CRITICAL"},
    "handlers": {"null": {"class": "logging.NullHandler"}},
}
