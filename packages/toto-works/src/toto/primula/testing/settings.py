"""Runnable settings for the Primula suite.

Same reasoning as ``toto.jess.testing.settings``: no host has *only* Primula, so the
app is exercised end to end against a settings module of its own. GIS is off via the
usual ``HAS_GIS`` + ``MIGRATION_MODULES`` pair so this runs on any interpreter with no
GDAL. ``toto.editor`` is installed because the suite proves the vault "open" button
routes ``sheet`` files to Primula *instead of* the generic ACE editor — that comparison
only exists when both plugin registries are populated.
"""
import tempfile
from pathlib import Path

from toto.registry import BASE_APPS

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "primula-suite-not-a-secret"
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
    # BASE_APPS rather than CORE_APPS: oya/header.html — which every Primula page
    # inherits — reverses `sso:login`/`sso:logout`, so the view tests need the auth
    # block to render pages at all.
    *BASE_APPS,
    "toto.editor",
    "toto.primula",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "toto.primula.testing.urls"

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
STATIC_ROOT = BASE_DIR / "staticfiles"
# Sheet bytes are real files: write them somewhere disposable, never the repo tree.
MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="primula-suite-media-"))

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("pl", "Polski")]
USE_I18N = True
USE_TZ = True

FIELD_ENCRYPTION_KEY = "zqx3Wt0nqTfKqBPXCsFtHQOMoO0v8kBn8ZQmFqBLLwo="
PLATFORM_DOMAIN = "primula.test"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"handlers": ["null"], "level": "CRITICAL"},
    "handlers": {"null": {"class": "logging.NullHandler"}},
}
