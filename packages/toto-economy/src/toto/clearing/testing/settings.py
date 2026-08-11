"""Runnable settings for the clearing suite.

Same reasoning as the other per-app testing modules (jess, primula): no host has
only clearing, so the app is exercised end to end against a settings module of
its own. Machine endpoints only — no oya chrome, so no auth url tree is needed.
GIS off via the usual pair, so this runs on any interpreter with no GDAL.
"""
from pathlib import Path

from toto.registry import BASE_APPS

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "clearing-suite-not-a-secret"
DEBUG = False
ALLOWED_HOSTS = ["*"]

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
    *BASE_APPS,
    "toto.assets",
    "toto.tariffs",
    "toto.clearing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "toto.clearing.testing.urls"

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
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

LANGUAGE_CODE = "en"
USE_I18N = True
USE_TZ = True

# Separate from FIELD_ENCRYPTION_KEY on purpose: see toto/assets/issuer.py.
MONETARY_ISSUER_KEY = "0Zk8Yl1ZQ0mQ0m0YlZk8Yl1ZQ0mQ0m0YlZk8Yl1ZQ0k="
FIELD_ENCRYPTION_KEY = "zqx3Wt0nqTfKqBPXCsFtHQOMoO0v8kBn8ZQmFqBLLwo="
PLATFORM_DOMAIN = "clearing.test"
CLEARING_SELF_PLATFORM_ID = "clearing-suite"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"handlers": ["null"], "level": "CRITICAL"},
    "handlers": {"null": {"class": "logging.NullHandler"}},
}
