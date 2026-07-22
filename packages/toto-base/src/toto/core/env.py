"""
Environment bootstrap for toto projects.

Call setup_env() as early as possible (top of manage.py / asgi entry point)
to load a .env file and apply dev defaults before Django settings are imported.
"""
import os
from pathlib import Path


# All environment variables used by toto + portal, with their dev defaults.
# Variables with None as default are required in production but optional in dev.
_DEV_DEFAULTS = {
    "DJANGO_ENV":      "dev",
    "DJANGO_HOST_IP":  "127.0.0.1",
    "REDIS_HOST":      "localhost",
    "ADMIN_PASSWORD":  "admin",
    "FULL_INGRESS":    "1",
    # SSO / backup vault — no default; must be set explicitly
    "SSO_VAULT_PASSWORD":    "",
    "BACKUP_VAULT_PASSWORD": "",
    # Sabbia agent-credential vault — no default; must be set explicitly
    "SABBIA_VAULT_PASSWORD": "",
}

# Variables required when DJANGO_ENV=PROD
_PROD_REQUIRED = [
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DB_HOST",
    "DB_PORT",
    "SSO_VAULT_PASSWORD",
]


def setup_env(env_file=None, warn_missing=True):
    """
    1. Locate and load a .env file (searches cwd and its parents).
    2. Apply dev defaults for any variable not already set.
    3. In PROD, warn about missing required variables.
    """
    _load_dotenv(_find_dotenv(env_file))

    is_prod = os.environ.get("DJANGO_ENV", "dev") == "PROD"

    for key, default in _DEV_DEFAULTS.items():
        if key not in os.environ:
            os.environ[key] = default

    if is_prod and warn_missing:
        missing = [k for k in _PROD_REQUIRED if not os.environ.get(k)]
        if missing:
            import warnings
            warnings.warn(
                f"PRODUCTION env is missing required variables: {', '.join(missing)}",
                stacklevel=2,
            )


def _find_dotenv(explicit=None):
    """Return a Path to the .env file, or None if not found."""
    if explicit is not None:
        p = Path(explicit)
        return p if p.exists() else None
    for directory in [Path.cwd(), *Path.cwd().parents]:
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
        # Stop at filesystem root or repo root markers
        if (directory / ".git").exists():
            break
    return None


def _load_dotenv(path):
    """
    Minimal .env parser.  Supports:
      KEY=value
      KEY="value with spaces"
      KEY='value'
      # comments
      blank lines
    Does NOT override variables already present in the environment.
    """
    if path is None:
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val
