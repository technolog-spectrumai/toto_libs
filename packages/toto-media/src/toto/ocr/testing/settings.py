"""Runnable settings for the text recognition suite.

Same reasoning as ``toto.kanban.testing.settings``: no host in this monorepo
installs ``toto.ocr`` any more — it left zenobia for placidia in 9/2026 — so
until this module existed its suites ran on no gate here, and a vault change
that broke it (the trash, 2026-10-01) could not be seen. Only what the app
stands on: the base apps (the vault holds the sources, quota meters the
pages) and the app itself. No geography: toto-base carries none since 2026-10-04 (``toto.locations`` is toto-geo's), so this runs on any interpreter with no GDAL.

Run from a host directory, whose manage.py puts the vendored sources first:

    manage.py test toto.ocr.tests_trashed_source --settings=toto.ocr.testing.settings

Nothing here reaches Tesseract or a worker: the tests mock the engine and
the queue, and the broker is in memory so a task sent by mistake goes
nowhere.
"""
import tempfile
from pathlib import Path

from toto.registry import BASE_APPS

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "ocr-suite-not-a-secret"
DEBUG = False
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "django_jsonform",
    "rest_framework",
    "colorfield",
    "trix_editor",
    # BASE_APPS rather than CORE_APPS: oya/header.html — which every page
    # here inherits — reverses `sso:login`/`sso:logout`.
    *BASE_APPS,
    "toto.ocr",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "toto.ocr.testing.urls"

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

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}

STATIC_URL = "static/"
# Temp dirs, never under packages/: see toto.kanban.testing.settings for the
# gitignored `staticfiles/` trap. The sources a run keeps go to MEDIA_ROOT.
STATIC_ROOT = Path(tempfile.mkdtemp(prefix="ocr-suite-static-"))
MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="ocr-suite-media-"))

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("pl", "Polski")]
USE_I18N = True
USE_TZ = True

FIELD_ENCRYPTION_KEY = "zqx3Wt0nqTfKqBPXCsFtHQOMoO0v8kBn8ZQmFqBLLwo="
PLATFORM_DOMAIN = "ocr.test"

CELERY_TASK_ALWAYS_EAGER = False
CELERY_BROKER_URL = "memory://"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"handlers": ["null"], "level": "CRITICAL"},
    "handlers": {"null": {"class": "logging.NullHandler"}},
}
