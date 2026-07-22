"""
Per-bucket pluggable storage driver for the Vault app.

Backends
--------
  local        — Django's configured default_storage (filesystem, etc.)
  s3           — boto3-backed S3-compatible store (AWS, OVH, MinIO, …)
  remote_toto  — another toto server's Vault API (read/write via HTTP)

Credentials are NEVER stored in the database.

S3 credentials come from the standard boto3 chain:
  1. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars
  2. ~/.aws/credentials or a named profile via storage_config["aws_profile"]
  3. IAM instance role / container credentials

Remote-toto token comes from the env var named in storage_config["api_token_env"]
(default: TOTO_REMOTE_API_TOKEN).

Non-secret S3 config lives in Bucket.storage_config:
  bucket_name       required — the S3 bucket name
  endpoint_url      optional — provider endpoint; resolved from Bucket.provider if absent
  region_name       optional
  prefix            optional — path prefix for all object keys, default "vault/"
  use_ssl           optional — bool, default True
  addressing_style  optional — "path" | "virtual" | "auto", default "auto"
  aws_profile       optional — named boto3 credentials profile

remote_toto config in Bucket.storage_config:
  server_url        required — base URL of the remote toto instance
  bucket_slug       required — slug of the bucket on the remote server
  api_token_env     optional — env var holding the API token (default TOTO_REMOTE_API_TOKEN)
"""
from __future__ import annotations

import logging
import os
import re

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._\-]")


def _safe_filename(name: str) -> str:
    """Return the basename of *name* with unsafe characters replaced by '_'."""
    base = os.path.basename(name.replace("\\", "/")) or "file"
    return _UNSAFE_RE.sub("_", base)


# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------

class BaseVaultStorageDriver:
    """
    Minimal read/write interface for vault file content.

    Convention:
    - *name* passed to read/exists/delete is the value stored in VaultFile.file
      (i.e. whatever save() returned when that file was first written).
    - *name* passed to save() is a filename hint; the actual stored key/path
      is determined by the driver and returned.
    """

    def read(self, name: str) -> bytes:
        """Return the full file content as bytes."""
        raise NotImplementedError

    def save(self, name: str, content: bytes) -> str:
        """Persist *content* and return the opaque key to store in VaultFile.file."""
        raise NotImplementedError

    def exists(self, name: str) -> bool:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Local driver
# ---------------------------------------------------------------------------

class LocalVaultStorageDriver(BaseVaultStorageDriver):
    """Thin wrapper around Django's configured default_storage."""

    def read(self, name: str) -> bytes:
        with default_storage.open(name, "rb") as fh:
            return fh.read()

    def save(self, name: str, content: bytes) -> str:
        # default_storage.save appends a suffix automatically on collision.
        return default_storage.save(name, ContentFile(content))

    def exists(self, name: str) -> bool:
        return default_storage.exists(name)

    def delete(self, name: str) -> None:
        if default_storage.exists(name):
            default_storage.delete(name)


# ---------------------------------------------------------------------------
# S3-compatible driver
# ---------------------------------------------------------------------------

class S3CompatibleVaultStorageDriver(BaseVaultStorageDriver):
    """boto3-backed driver for AWS S3 and S3-compatible stores (OVH, MinIO, …)."""

    def __init__(self, config: dict):
        self._config = config
        self._client = None  # lazy — built on first use

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self):
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self):
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            raise RuntimeError(
                "boto3 is required for S3 storage backends. "
                "Install it with: pip install boto3"
            )

        profile = self._config.get("aws_profile")
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()

        client_kwargs: dict = {}
        if endpoint_url := self._config.get("endpoint_url"):
            client_kwargs["endpoint_url"] = endpoint_url
        if region_name := self._config.get("region_name"):
            client_kwargs["region_name"] = region_name
        if not self._config.get("use_ssl", True):
            client_kwargs["use_ssl"] = False

        addressing_style = self._config.get("addressing_style", "auto")
        client_kwargs["config"] = Config(s3={"addressing_style": addressing_style})

        return session.client("s3", **client_kwargs)

    @property
    def _bucket_name(self) -> str:
        name = self._config.get("bucket_name")
        if not name:
            raise ValueError("S3 storage_config must include 'bucket_name'.")
        return name

    @property
    def _prefix(self) -> str:
        raw = self._config.get("prefix", "vault/")
        if not raw:
            return ""
        return raw.rstrip("/") + "/"

    def _unique_object_key(self, filename_hint: str) -> str:
        """Build a unique S3 object key from a filename hint."""
        safe = _safe_filename(filename_hint)
        root, ext = os.path.splitext(safe)
        suffix = os.urandom(4).hex()
        return f"{self._prefix}{root}_{suffix}{ext}"

    # ------------------------------------------------------------------
    # Driver interface
    # ------------------------------------------------------------------

    def read(self, name: str) -> bytes:
        # name is the full S3 key as returned by save() and stored in VaultFile.file
        response = self._get_client().get_object(Bucket=self._bucket_name, Key=name)
        return response["Body"].read()

    def save(self, name: str, content: bytes) -> str:
        key = self._unique_object_key(name)
        self._get_client().put_object(Bucket=self._bucket_name, Key=key, Body=content)
        return key

    def exists(self, name: str) -> bool:
        try:
            self._get_client().head_object(Bucket=self._bucket_name, Key=name)
            return True
        except Exception:
            return False

    def delete(self, name: str) -> None:
        try:
            self._get_client().delete_object(Bucket=self._bucket_name, Key=name)
        except Exception as exc:
            logger.warning("S3 delete failed for key %r: %s", name, exc)


# ---------------------------------------------------------------------------
# Remote Toto driver
# ---------------------------------------------------------------------------

class RemoteTotoStorageDriver(BaseVaultStorageDriver):
    """
    Reads/writes files from another toto server's Vault file API.

    The API token is read from the environment variable named by
    storage_config["api_token_env"] (default: TOTO_REMOTE_API_TOKEN).
    """

    def __init__(self, server_url: str, bucket_slug: str, api_token: str):
        self._base = server_url.rstrip("/")
        self._slug = bucket_slug
        self._token = api_token
        self._session = None

    def _get_session(self):
        if self._session is None:
            try:
                import requests as req
            except ImportError:
                raise RuntimeError(
                    "requests is required for remote_toto backend: pip install requests"
                )
            self._session = req.Session()
            if self._token:
                self._session.headers["Authorization"] = f"Token {self._token}"
        return self._session

    def _files_url(self, key: str = "") -> str:
        base = f"{self._base}/api/vault/buckets/{self._slug}/files/"
        return f"{base}{key}" if key else base

    def read(self, name: str) -> bytes:
        resp = self._get_session().get(self._files_url(name))
        resp.raise_for_status()
        return resp.content

    def save(self, name: str, content: bytes) -> str:
        import io
        resp = self._get_session().post(
            self._files_url(),
            files={"file": (_safe_filename(name), io.BytesIO(content))},
        )
        resp.raise_for_status()
        return resp.json()["key"]

    def exists(self, name: str) -> bool:
        resp = self._get_session().head(self._files_url(name))
        return resp.status_code == 200

    def delete(self, name: str) -> None:
        try:
            self._get_session().delete(self._files_url(name)).raise_for_status()
        except Exception as exc:
            logger.warning("remote_toto delete failed for %r: %s", name, exc)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_bucket_storage(bucket) -> BaseVaultStorageDriver:
    """Return the appropriate storage driver for *bucket*."""
    backend = getattr(bucket, "storage_backend", None) or "local"
    config: dict = getattr(bucket, "storage_config", None) or {}

    if backend == "s3":
        merged = dict(config)
        # Fill in provider defaults when the bucket has a linked provider
        # and the config does not already override these values.
        if not merged.get("endpoint_url") and getattr(bucket, "provider_id", None):
            provider = bucket.provider
            region = merged.get("region_name") or provider.default_region
            endpoint_url = provider.resolve_endpoint_url(region=region)
            if endpoint_url:
                merged["endpoint_url"] = endpoint_url
            if not merged.get("addressing_style"):
                merged["addressing_style"] = provider.addressing_style
            if "use_ssl" not in merged:
                merged["use_ssl"] = provider.use_ssl
        return S3CompatibleVaultStorageDriver(merged)

    if backend == "remote_toto":
        server_url = config.get("server_url", "")
        bucket_slug = config.get("bucket_slug", "")
        token_env = config.get("api_token_env", "TOTO_REMOTE_API_TOKEN")
        api_token = os.environ.get(token_env, "")
        return RemoteTotoStorageDriver(server_url, bucket_slug, api_token)

    return LocalVaultStorageDriver()
