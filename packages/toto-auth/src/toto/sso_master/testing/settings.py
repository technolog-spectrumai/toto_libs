"""Runnable settings for the sso_master suite.

These 61 tests ran in **no gate at all**. They could not: the only shipped
runnable settings module is the federation harness, which is consumer-shaped —
its `sso` namespace resolves to `sso_client`, so `sso:login` is a federated
redirect rather than the provider's own page, and half the suite fails on that
alone (13 failures / 7 errors, none of them real).

A provider-shaped tree fixes it. Two things here are not decoration:

* ``SECURE_REDIRECT_EXEMPT`` — ``test_internal_oidc_paths_exempt_from_ssl_redirect``
  asserts the internal discovery and jwks paths answer 200 under
  ``SECURE_SSL_REDIRECT``, because sidecar containers call them server-side over
  plaintext with no ``X-Forwarded-Proto``. Without this the test fails against a
  correct implementation.
* ``toto.api`` is mounted — ``test_register_returns_token_that_works_as_bearer``
  exchanges its session key at an endpoint that lives there.

GIS is off via the usual ``HAS_GIS`` + ``MIGRATION_MODULES`` pair, so it runs on
any interpreter with no GDAL.
"""
from pathlib import Path

from toto.registry import CORE_APPS

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "sso-master-suite-not-a-secret"
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
    *CORE_APPS,
    # The provider half, and only it: a host is one or the other.
    "toto.sso_core",
    "toto.sso_master",
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

ROOT_URLCONF = "toto.sso_master.testing.urls"

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
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("pl", "Polski")]
USE_I18N = True
USE_TZ = True

FIELD_ENCRYPTION_KEY = "zqx3Wt0nqTfKqBPXCsFtHQOMoO0v8kBn8ZQmFqBLLwo="
PLATFORM_DOMAIN = "provider.test"

# Real, not a placeholder: the suite runs Argon2id and the four-tier gervazy
# envelope, because a mocked vault would prove nothing about whether a signing
# key can actually be decrypted.
SSO_VAULT_PASSWORD = "sso-master-suite-vault-passphrase"
FEDERATION_KEY = "sso-master-suite-federation-key"

# Sidecars call these server-side over the compose network, on plaintext, with no
# X-Forwarded-Proto. See the module docstring.
SECURE_REDIRECT_EXEMPT = [
    r"^\.well-known/openid-configuration-internal$",
    r"^sso/token/$",
    r"^sso/userinfo/$",
    r"^sso/jwks\.json$",
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"handlers": ["null"], "level": "CRITICAL"},
    "handlers": {"null": {"class": "logging.NullHandler"}},
}
