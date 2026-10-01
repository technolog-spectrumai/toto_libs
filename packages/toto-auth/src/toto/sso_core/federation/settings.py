"""Runnable settings for the federation suite: BOTH sides in one process.

No real host has this shape — a host is a provider or a consumer, never both —
so the pair can only be exercised against a settings module of its own.

GIS is off, via the same HAS_GIS + MIGRATION_MODULES pair that
``tests/settings_min_nogis.py`` already proves, so the suite runs on any
interpreter with no GDAL.
"""
from pathlib import Path

from toto.registry import CORE_APPS

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "federation-suite-not-a-secret"
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
    "reversion",
    "trix_editor",
    *CORE_APPS,
    # Both halves of the federation. auth_apps() would return one or the other;
    # here we want the pair.
    "toto.sso_core",
    "toto.sso_master",
    "toto.sso_client",
    "toto.social_login",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

# The consumer's tree is the default; the bridge swaps in the provider's for the
# duration of a single hop. See bridge.provider_urlconf.
ROOT_URLCONF = "toto.sso_core.federation.consumer_urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        # APP_DIRS so the consent page and the login page render for real —
        # an unguarded {% url %} in either is a failure this suite should see.
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
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("pl", "Polski")]
USE_I18N = True
USE_TZ = True

# The signing key's private half is encrypted in a Gervazy strongbox exactly as
# it is in production — the ID token this suite verifies is genuinely RS256.
SSO_VAULT_PASSWORD = "federation-suite-vault"
FIELD_ENCRYPTION_KEY = "zqx3Wt0nqTfKqBPXCsFtHQOMoO0v8kBn8ZQmFqBLLwo="

PLATFORM_DOMAIN = "provider.test"

# Off by default, like a real consumer host; tests opt in per-case.
TOTO_SSO_AUTO_PROVISION = False
TOTO_SOCIAL_SIGNUP = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"handlers": ["null"], "level": "CRITICAL"},
    "handlers": {"null": {"class": "logging.NullHandler"}},
}
