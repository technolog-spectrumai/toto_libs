"""
Per-bucket pluggable storage driver for the Vault app.

Local buckets delegate to Django's configured default_storage.
S3-compatible buckets use boto3 with an optional endpoint_url for OVH, MinIO, etc.

Credentials are never stored in the database.  The S3 driver reads them from
the standard boto3 credential chain:
  1. Environment variables (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
  2. ~/.aws/credentials, or a named profile via storage_config["aws_profile"]
  3. IAM instance role / container credentials

Non-secret S3 config lives in Bucket.storage_config:
  bucket_name       required — the S3 bucket name
  endpoint_url      optional — override for OVH / MinIO / custom providers
  region_name       optional
  prefix            optional — path prefix for all object keys, default "vault/"
  use_ssl           optional — bool, default True
  addressing_style  optional — "path" | "virtual" | "auto", default "auto"
  aws_profile       optional — named boto3 credentials profile
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
# Factory
# ---------------------------------------------------------------------------

def get_bucket_storage(bucket) -> BaseVaultStorageDriver:
    """Return the appropriate storage driver for *bucket*."""
    backend = getattr(bucket, "storage_backend", None) or "local"
    config = getattr(bucket, "storage_config", None) or {}
    if backend == "s3":
        return S3CompatibleVaultStorageDriver(config)
    return LocalVaultStorageDriver()
