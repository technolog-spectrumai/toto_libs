"""
Shared Django settings builder for toto deployments.

Usage in your project's settings.py:
    from pathlib import Path
    from toto.conf.settings_builder import build_settings
    BASE_DIR = Path(__file__).resolve().parent.parent
    globals().update(build_settings(BASE_DIR))

Requires TOTO_CONFIG env var pointing to a deployment YAML config file.
"""
from __future__ import annotations

import base64
import logging
import os
import secrets
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise ImportError(
        "PyYAML is required for toto config. Install it with: pip install PyYAML"
    ) from exc


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_config(config_path: str | Path | None = None) -> dict:
    """Load and return the YAML deployment config."""
    if config_path is None:
        config_path = os.environ.get("TOTO_CONFIG")

    if not config_path:
        raise RuntimeError(
            "TOTO_CONFIG environment variable is not set.\n"
            "Point it to your deployment YAML config file:\n"
            "  export TOTO_CONFIG=/path/to/configs/portal_mini.yaml\n"
            "Or pass the path explicitly to build_settings(BASE_DIR, config_path='...')"
        )

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"TOTO_CONFIG file not found: {path}\n"
            "Check that the path is correct and the file exists."
        )

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"TOTO_CONFIG file is not a valid YAML mapping: {path}")

    return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generated_fernet_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _env_list(name: str, default: str = "") -> list[str]:
    val = os.getenv(name, default)
    return [item.strip() for item in val.split(",") if item.strip()]


def _app_to_django_path(app: str) -> str:
    """Convert 'module:callable' notation to Django's 'module.callable' dot notation."""
    return app.replace(":", ".")


def _default_middleware(services: dict) -> list[str]:
    """Build default middleware list based on enabled services."""
    mw = []

    if services.get("prometheus"):
        mw.append("django_prometheus.middleware.PrometheusBeforeMiddleware")

    mw.extend([
        "toto.core.middleware.PlatformMiddleware",
        "django.middleware.security.SecurityMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
    ])

    if services.get("cors"):
        mw.append("corsheaders.middleware.CorsMiddleware")

    mw.extend([
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
        "whitenoise.middleware.WhiteNoiseMiddleware",
        "django.middleware.locale.LocaleMiddleware",
    ])

    if services.get("prometheus"):
        mw.append("django_prometheus.middleware.PrometheusAfterMiddleware")

    return mw


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_settings(base_dir: Path, config_path: str | Path | None = None) -> dict[str, Any]:
    """
    Build a complete Django settings dict from the YAML config.

    :param base_dir:     Project BASE_DIR — Path(__file__).resolve().parent.parent in settings.py.
    :param config_path:  Optional config override. Falls back to TOTO_CONFIG env var.
    :returns:            Dict suitable for ``globals().update(build_settings(BASE_DIR))``.
    """
    cfg = load_config(config_path)

    env_cfg = cfg.get("environment", {})
    services = cfg.get("services", {})
    server_cfg = cfg.get("server", {})
    db_cfg = cfg.get("database", {})
    redis_cfg = cfg.get("redis", {})

    # ------------------------------------------------------------------ core
    django_env = os.getenv("DJANGO_ENV", str(env_cfg.get("DJANGO_ENV", "dev")))
    debug_default = "1" if django_env != "PROD" else "0"
    debug = os.getenv("DEBUG", str(env_cfg.get("DEBUG", debug_default))) == "1"

    if debug:
        logging.basicConfig(level=logging.DEBUG)

    deployment_name = cfg.get("deployment", {}).get("name", "portal")

    # ------------------------------------------------------------------ apps
    installed_apps: list[str] = list(cfg.get("installed_apps", []))

    # Auto-inject framework apps required by enabled services
    if services.get("prometheus") and "django_prometheus" not in installed_apps:
        installed_apps.insert(0, "django_prometheus")
    if services.get("websockets") and "channels" not in installed_apps:
        installed_apps.insert(0, "channels")

    # ------------------------------------------------------------ middleware
    middleware: list[str] = list(cfg.get("middleware") or _default_middleware(services))

    # ------------------------------------------------------------ server type
    server_type = server_cfg.get("type", "wsgi")  # "wsgi" | "asgi"
    if server_type == "asgi":
        asgi_app = server_cfg.get("app", f"{deployment_name}.asgi:application")
        server_app_settings: dict[str, Any] = {
            "ASGI_APPLICATION": _app_to_django_path(asgi_app)
        }
    else:
        wsgi_app = server_cfg.get("app", f"{deployment_name}.wsgi:application")
        server_app_settings = {
            "WSGI_APPLICATION": _app_to_django_path(wsgi_app)
        }

    # ------------------------------------------------------------ database
    db_engine = db_cfg.get("engine", "spatialite")
    use_postgres = db_engine == "postgis"

    if use_postgres:
        databases: dict[str, Any] = {
            "default": {
                "ENGINE": "django.contrib.gis.db.backends.postgis",
                "NAME": os.getenv("DB_NAME", db_cfg.get("name", "db")),
                "USER": os.getenv("DB_USER", db_cfg.get("user", "user")),
                "PASSWORD": os.getenv("DB_PASSWORD", db_cfg.get("password", "")),
                "HOST": os.getenv("DB_HOST", db_cfg.get("host", "postgres")),
                "PORT": os.getenv("DB_PORT", str(db_cfg.get("port", 5432))),
            }
        }
    else:
        databases = {
            "default": {
                "ENGINE": "django.contrib.gis.db.backends.spatialite",
                "NAME": base_dir.parent / "db.sqlite3",
            }
        }

    # ---------------------------------------------------------------- cache
    cache_url = os.getenv(
        "REDIS_CACHE_URL",
        redis_cfg.get("cache_url", "redis://127.0.0.1:6379/1"),
    )
    caches: dict[str, Any] = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": cache_url,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        }
    }

    # --------------------------------------------------------------- storage
    storages: dict[str, Any] = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        },
    }

    # ---------------------------------------------------------------- neo4j
    neo4j_settings: dict[str, Any] = {}
    if services.get("neo4j"):
        neo4j_cfg = cfg.get("neo4j", {})
        neo4j_uri = os.environ.get(
            "NEO4J_URI", f"bolt://{neo4j_cfg.get('host', 'neo4j')}:7687"
        )
        neo4j_user = os.environ.get("NEO4J_USER", neo4j_cfg.get("user", "neo4j"))
        neo4j_password = os.environ.get(
            "NEO4J_PASSWORD", neo4j_cfg.get("password", "neo4j-admin")
        )
        bolt_url = f"bolt://{neo4j_user}:{neo4j_password}@{neo4j_uri.replace('bolt://', '')}"
        neo4j_settings = {
            "RAVIOLI_ENABLED": True,
            "NEO4J_URI": neo4j_uri,
            "NEO4J_USER": neo4j_user,
            "NEO4J_PASSWORD": neo4j_password,
        }

    # --------------------------------------------------------------- celery
    celery_settings: dict[str, Any] = {}
    if services.get("celery"):
        broker_url = os.getenv(
            "CELERY_BROKER_URL",
            os.getenv("REDIS_URL", redis_cfg.get("url", "redis://127.0.0.1:6379/0")),
        )
        celery_settings = {
            "CELERY_BROKER_URL": broker_url,
            "CELERY_RESULT_BACKEND": os.getenv("CELERY_RESULT_BACKEND", broker_url),
            "CELERY_TASK_TRACK_STARTED": True,
            "CELERY_TASK_TIME_LIMIT": 60 * 30,
        }
        if "toto.game" in installed_apps:
            celery_settings["CELERY_BEAT_SCHEDULE"] = {
                "toto-game-global-tick": {
                    "task": "toto.game.tasks.run_global_game_tick",
                    "schedule": int(os.getenv("GAME_TICK_SECONDS", "300")),
                },
            }

    # ---------------------------------------------------------- websockets
    channel_settings: dict[str, Any] = {}
    if services.get("websockets"):
        channel_url = os.getenv(
            "CHANNEL_REDIS_URL",
            os.getenv("REDIS_URL", redis_cfg.get("url", "redis://127.0.0.1:6379/0")),
        )
        channel_settings = {
            "CHANNEL_LAYERS": {
                "default": {
                    "BACKEND": "channels_redis.core.RedisChannelLayer",
                    "CONFIG": {"hosts": [channel_url]},
                }
            }
        }

    # ------------------------------------------------------- kernel server
    kernel_settings: dict[str, Any] = {}
    if services.get("kernel_server"):
        kernel_settings = {
            "KERNEL_SERVER_ADDR": os.getenv("KERNEL_SERVER_ADDR", "tcp://127.0.0.1:5555")
        }

    # ---------------------------------------------------- allowed hosts / CSRF
    _host_list = list(cfg.get("allowed_hosts", ["localhost", "127.0.0.1"]))
    _nginx_names = cfg.get("nginx", {}).get("server_names", [])
    for _h in _nginx_names:
        if _h not in _host_list:
            _host_list.append(_h)
    allowed_hosts_default = ",".join(_host_list)
    allowed_hosts = _env_list("ALLOWED_HOSTS", allowed_hosts_default)

    if django_env == "PROD":
        csrf_default = ",".join(cfg.get("csrf_trusted_origins", []))
        csrf_origins = _env_list("CSRF_TRUSTED_ORIGINS", csrf_default)
    else:
        csrf_origins = [
            "http://127.0.0.1:8000",
            "http://127.0.0.1:8080",
            "http://localhost:8000",
            "http://localhost:8080",
        ]

    # ------------------------------------------------------- security (prod)
    security_settings: dict[str, Any] = {}
    if django_env == "PROD":
        security_settings = {
            "SECURE_SSL_REDIRECT": not debug,
            "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
            "SESSION_COOKIE_SECURE": not debug,
            "CSRF_COOKIE_SECURE": not debug,
            "CSRF_COOKIE_HTTPONLY": True,
        }

    # ---------------------------------------------------------------- login
    login_url_settings: dict[str, Any] = {}
    login_url_name = cfg.get("login_url")
    if login_url_name:
        from django.urls import reverse_lazy  # noqa: PLC0415
        login_url_settings = {"LOGIN_URL": reverse_lazy(login_url_name)}

    # -------------------------------------------------------------- logging
    log_dir = base_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    installed_loggers: dict[str, Any] = {}
    for app in installed_apps:
        label = app.split(".")[-1]
        installed_loggers[label] = {
            "handlers": [f"{label}_file"],
            "level": "INFO",
            "propagate": False,
        }

    logging_config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": "[%(name)s] %(asctime)s %(levelname)s %(message)s"}
        },
        "handlers": {
            f"{label}_file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "filename": str(log_dir / f"{label}.log"),
                "when": "midnight",
                "backupCount": 7,
                "formatter": "default",
            }
            for label in installed_loggers
        },
        "loggers": installed_loggers,
    }

    # ---------------------------------------------------------- app-specific
    locations_settings: dict[str, Any] = {}
    if "toto.locations" in installed_apps:
        locations_settings["LOCATIONS_GEOCODING"] = cfg.get(
            "locations_geocoding",
            {
                "enabled": True,
                "reverse_url": "https://nominatim.openstreetmap.org/reverse",
                "search_url": "https://nominatim.openstreetmap.org/search",
                "user_agent": "toto-locations/1.0",
                "timeout": 8,
                "accept_language": "en",
                "fail_silently": True,
                "search_limit": 5,
            },
        )

    spatialite_settings: dict[str, Any] = {}
    if not use_postgres:
        spatialite_settings["SPATIALITE_LIBRARY_PATH"] = "mod_spatialite"

    # ------------------------------------------------------ URL / root conf
    root_urlconf = cfg.get("deployment", {}).get(
        "root_urlconf", f"{deployment_name}.urls"
    )

    migration_modules = cfg.get("migration_modules", {})

    # ----------------------------------------------------------- assemble
    result: dict[str, Any] = {
        # Security
        "SECRET_KEY": os.getenv("SECRET_KEY") or secrets.token_urlsafe(50),
        "DEBUG": debug,
        "ALLOWED_HOSTS": allowed_hosts,
        "CSRF_TRUSTED_ORIGINS": csrf_origins,
        "CSRF_ALLOWED_ORIGINS": csrf_origins,
        "USE_X_FORWARDED_HOST": True,
        "DJANGO_ENV": django_env,
        # Apps & routing
        "INSTALLED_APPS": installed_apps,
        "MIDDLEWARE": middleware,
        "AUTHENTICATION_BACKENDS": ["django.contrib.auth.backends.ModelBackend"],
        "ROOT_URLCONF": root_urlconf,
        # Storage
        "STORAGES": storages,
        # Templates
        "TEMPLATES": [
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
                    ]
                },
            }
        ],
        # Database
        "DATABASES": databases,
        # Auth
        "AUTH_PASSWORD_VALIDATORS": [
            {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
            {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
            {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
            {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
        ],
        # i18n
        "LANGUAGE_CODE": "en-us",
        "TIME_ZONE": "UTC",
        "USE_I18N": True,
        "USE_TZ": True,
        # Static / media
        "STATIC_URL": "/static/",
        "STATIC_ROOT": str(base_dir / "staticfiles"),
        "MEDIA_URL": "/media/",
        "MEDIA_ROOT": str(base_dir / "media"),
        "DEFAULT_AUTO_FIELD": "django.db.models.BigAutoField",
        # Cache
        "CACHES": caches,
        # Secrets
        "FIELD_ENCRYPTION_KEY": os.getenv("FIELD_ENCRYPTION_KEY") or _generated_fernet_key(),
        "SSO_VAULT_PASSWORD": os.getenv("SSO_VAULT_PASSWORD", ""),
        # Toto-specific
        "FULL_INGRESS": os.getenv("FULL_INGRESS", "0") == "1",
        "LOGIN_RETRY_COOLDOWN_SECONDS": int(os.getenv("LOGIN_RETRY_COOLDOWN_SECONDS", "3")),
        "INGRESS_ALLOWED_APPS": cfg.get("ingress_allowed_apps", []),
        "APPS_TO_SYNC": cfg.get("apps_to_sync", []),
        "PLATFORM_LOGO_PATH": os.path.join("..", "data", "img", "platform_logo.png"),
        "DASHBOARD_ITEMS": cfg.get("dashboard_items", []),
        "HEADER_NAV_ITEMS": cfg.get("header_nav", []),
        "TOTO_ADMIN_READONLY": cfg.get("toto_admin_readonly", False),
        "GAME_ENGINE_CONFIG_PATH": os.getenv("GAME_ENGINE_CONFIG_PATH", cfg.get("game_engine_config_path", "")),
        "ACME_CHALLENGE_ROOT": str(base_dir / "acme-challenges"),
        # Email
        "EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend",
        # Logging
        "LOGGING": logging_config,
        # Server
        **server_app_settings,
        # Optional services
        **security_settings,
        **neo4j_settings,
        **celery_settings,
        **channel_settings,
        **kernel_settings,
        **login_url_settings,
        **locations_settings,
        **spatialite_settings,
    }

    if migration_modules:
        result["MIGRATION_MODULES"] = migration_modules

    return result
