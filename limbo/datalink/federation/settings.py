"""Runnable settings for the datalink suite: two peers, one process, two databases.

Modelled on ``toto.sso_core.federation.settings``, with the differences that matter
called out below. See the package docstring for why two databases are not optional.

GIS is off, via the same HAS_GIS + MIGRATION_MODULES pair ``tests/settings_min_nogis.py``
already proves, so the suite runs on any interpreter with no GDAL. That also means the
GIS-off half of the geometry contract is what runs by default — the geometry fields are
absent from the locations models entirely, which is exactly the ``optional_fields`` case.
"""
from pathlib import Path

from toto.registry import CORE_APPS

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "datalink-suite-not-a-secret"
DEBUG = False
ALLOWED_HOSTS = ["*"]

# locations loads without geometry; see the module docstring.
HAS_GIS = False

# No auth app on purpose — see the package docstring. datalink is a toto-base app and
# toto-base cannot depend on toto-auth, so the suite must prove datalink works with no
# federation installed at all.
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
    "toto.datalink",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

# The receiver's tree is the default; bridge.peer_side swaps in the peer's for the
# duration of a single hop, together with the database.
ROOT_URLCONF = "toto.datalink.federation.receiver_urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        # APP_DIRS so the staff pages render for real — an unguarded {% url %} or an
        # unescaped peer-supplied string is a failure this suite should see.
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
    # The receiver: where a run executes and the only side that ever writes.
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    # The peer: the source, which only serves reads. Django derives a distinct test
    # database per alias, so two in-memory databases do not collide.
    "peer": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}
DATABASE_ROUTERS = ["toto.datalink.federation.routers.PeerRouter"]

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("pl", "Polski")]
USE_I18N = True
USE_TZ = True

FIELD_ENCRYPTION_KEY = "zqx3Wt0nqTfKqBPXCsFtHQOMoO0v8kBn8ZQmFqBLLwo="
PLATFORM_DOMAIN = "receiver.test"

# Both sides serve here, because the suite needs a peer that answers. A real host
# defaults this off: installing datalink must not open a read surface.
DATALINK_SERVE = True

# Set deliberately non-empty, so the suite also proves datalink honours the outbound
# host allowlist and that a hardcoded host cannot leak in.
API_CONNECTOR_ALLOWED_HOSTS = ["peer.test", "receiver.test"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"handlers": ["null"], "level": "CRITICAL"},
    "handlers": {"null": {"class": "logging.NullHandler"}},
}
