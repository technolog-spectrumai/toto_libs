"""Runnable settings for the knowledge graph's doors into the vault.

Same reasoning as ``toto.ocr.testing.settings``: no host in this monorepo
installs ``toto.ravioli`` any more — zenobia does not carry the graph tier —
so a test of its views ran on no gate here, and a vault rule they must follow
(no Office names, 2026-10-01) could not be seen. Only what those doors stand
on: the base apps (the vault holds what they write), the app itself, and
``toto.neo_editor``, whose url the NeoJSON export answers with. The workflow
engine is left out: the analysis door reaches it only through
``views._trigger_workflow``, which the tests stand in for, and the task
module imports nothing of it but the registry. GIS is off via the usual
``HAS_GIS`` + ``MIGRATION_MODULES`` pair, so this runs on any interpreter
with no GDAL.

Run from a host directory, whose manage.py puts the vendored sources first:

    manage.py test toto.ravioli.tests.test_office_names --settings=toto.ravioli.testing.settings

Nothing here reaches Neo4j or a worker: the export reads a query's cached
result, ``RAVIOLI_ENABLED`` is off, and the broker is in memory so a task
sent by mistake goes nowhere.
"""
import tempfile
from pathlib import Path

from toto.registry import BASE_APPS

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "ravioli-suite-not-a-secret"
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
    # BASE_APPS rather than CORE_APPS, as in the ocr harness: oya/header.html
    # reverses `sso:login`/`sso:logout` on every page.
    *BASE_APPS,
    "toto.ravioli",
    "toto.neo_editor",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "toto.ravioli.testing.urls"

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
# Temp dirs, never under packages/: see toto.kanban.testing.settings for the
# gitignored `staticfiles/` trap. The files an export writes go to MEDIA_ROOT.
STATIC_ROOT = Path(tempfile.mkdtemp(prefix="ravioli-suite-static-"))
MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="ravioli-suite-media-"))

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("pl", "Polski")]
USE_I18N = True
USE_TZ = True

FIELD_ENCRYPTION_KEY = "zqx3Wt0nqTfKqBPXCsFtHQOMoO0v8kBn8ZQmFqBLLwo="
PLATFORM_DOMAIN = "ravioli.test"

RAVIOLI_ENABLED = False

CELERY_TASK_ALWAYS_EAGER = False
CELERY_BROKER_URL = "memory://"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"handlers": ["null"], "level": "CRITICAL"},
    "handlers": {"null": {"class": "logging.NullHandler"}},
}
