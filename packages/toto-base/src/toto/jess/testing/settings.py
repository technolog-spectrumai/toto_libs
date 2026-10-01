"""Runnable settings for the Jess suite.

No real host has this exact shape — a host installs Jess alongside a dozen other
features — so the app can only be exercised end to end against a settings module of its
own. Same reasoning as
``sso_core/federation/settings.py``, which exist for the same reason.

Two things here are load-bearing rather than incidental:

* ``EMAIL_BACKEND`` names Jess, which is what a host with ``BUILD_JESS=1`` does. The
  suite then overrides it per test to prove the locmem/console paths bypass Jess
  entirely — see ``toto/core/email_config.py`` for why that dispatch matters.
* ``CELERY_TASK_ALWAYS_EAGER`` is **off**. Jess's whole contract is that a request never
  blocks on SMTP, so a suite that silently ran the task inline would be testing a
  different program. Tests that want the send to happen call the task function directly.

GIS is off, via the same ``HAS_GIS`` + ``MIGRATION_MODULES`` pair the other suites use,
so this runs on any interpreter with no GDAL.
"""
from pathlib import Path

from toto.registry import BASE_APPS

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "jess-suite-not-a-secret"
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
    "rest_framework",
    "colorfield",
    "trix_editor",
    # BASE_APPS rather than CORE_APPS, because oya/header.html — the shared header
    # every Jess page inherits — reverses `sso:login` and `sso:logout`. A suite that
    # stubbed the header would stop being able to prove the pages render at all, which
    # is most of what these view tests are for.
    *BASE_APPS,
    "toto.jess",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "toto.jess.testing.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        # APP_DIRS so the staff pages render for real. Jess's templates extend
        # oya/base.html and drive Alpine from a json_script payload; a suite that stubbed
        # the renderer would not have caught the multi-line {# #} comments that render as
        # visible page text.
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
LANGUAGES = [("en", "English"), ("pl", "Polski")]
USE_I18N = True
USE_TZ = True

FIELD_ENCRYPTION_KEY = "zqx3Wt0nqTfKqBPXCsFtHQOMoO0v8kBn8ZQmFqBLLwo="
PLATFORM_DOMAIN = "jess.test"

# What a BUILD_JESS=1 host sets. Individual tests override it to prove the other paths.
EMAIL_BACKEND = "toto.jess.backend.JessEmailBackend"
DEFAULT_FROM_EMAIL = "platform@jess.test"

# Jess's own strongbox passphrase. A real value, not a placeholder: the suite runs
# Argon2id and the real four-tier gervazy envelope, because a mocked vault would prove
# nothing about whether a stored SMTP password can be read back.
JESS_VAULT_PASSWORD = "jess-suite-vault-passphrase"

# Deliberately NOT eager — see the module docstring.
CELERY_TASK_ALWAYS_EAGER = False
CELERY_BROKER_URL = "memory://"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"handlers": ["null"], "level": "CRITICAL"},
    "handlers": {"null": {"class": "logging.NullHandler"}},
}
