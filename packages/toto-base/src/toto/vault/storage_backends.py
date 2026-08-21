"""
Per-bucket pluggable storage driver for the Vault app.

Backends
--------
  local        — this host's private vault storage (VAULT_ROOT / MEDIA_ROOT)
  s3           — boto3-backed S3-compatible store (AWS, OVH, MinIO, …)
  remote_toto  — a paired toto host's exported bucket, via the peer API

Credentials are NEVER stored in the database (the peer's api key is the one
exception, and it is Fernet-sealed on the BucketPeer row — see
toto/vault/peering.py).

S3 credentials come from the standard boto3 chain:
  1. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars
  2. ~/.aws/credentials or a named profile via storage_config["aws_profile"]
  3. IAM instance role / container credentials

Non-secret S3 config lives in Bucket.storage_config:
  bucket_name       required — the S3 bucket name
  endpoint_url      optional — provider endpoint; resolved from Bucket.provider if absent
  region_name       optional
  prefix            optional — path prefix for all object keys, default "vault/"
  use_ssl           optional — bool, default True
  addressing_style  optional — "path" | "virtual" | "auto", default "auto"
  aws_profile       optional — named boto3 credentials profile

remote_toto needs NO storage_config at all: Bucket.peer is the whole
transport identity, so a bucket listing can never leak a host URL or a
token name.
"""
from __future__ import annotations

import logging
import os
import re

from django.core.files.base import ContentFile

from toto.vault.storage import private_storage

logger = logging.getLogger(__name__)

#: The ceiling for a single file written to a NON-local backend. External
#: writes hold the whole body in memory (they already did on the scan path),
#: and a network hop makes that a wire-sized problem rather than a disk-sized
#: one — so the cap is explicit instead of discovered under load.
EXTERNAL_UPLOAD_MAX_BYTES = 512 * 2**20


class UploadRefused(RuntimeError):
    """This bucket cannot take a direct upload; the message says why."""

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

    def open(self, name: str):
        """A binary file-like over the content, for streaming responses.

        The default buffers ``read()`` — correct everywhere, efficient only
        locally. Drivers whose transport can hand back a real stream override it:
        S3 returns botocore's StreamingBody, and a remote peer returns the
        HTTP response's raw stream, so a download never holds the whole file
        in this process's memory.
        """
        import io

        return io.BytesIO(self.read(name))

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
    """Thin wrapper around the vault's own private storage.

    ``private_storage()``, NOT ``default_storage`` — the FileField writes
    through the former (``VAULT_ROOT or MEDIA_ROOT``), and on a host that
    sets ``VAULT_ROOT`` a driver wrapping default_storage read and purged
    the wrong root entirely. The two must be the same disk by construction.
    """

    def _storage(self):
        return private_storage()

    def read(self, name: str) -> bytes:
        with self._storage().open(name, "rb") as fh:
            return fh.read()

    def open(self, name: str):
        return self._storage().open(name, "rb")

    def save(self, name: str, content: bytes) -> str:
        # FileSystemStorage.save appends a suffix automatically on collision.
        return self._storage().save(name, ContentFile(content))

    def exists(self, name: str) -> bool:
        return self._storage().exists(name)

    def delete(self, name: str) -> None:
        storage = self._storage()
        if storage.exists(name):
            storage.delete(name)


# ---------------------------------------------------------------------------
# S3-compatible driver
# ---------------------------------------------------------------------------

class S3CompatibleVaultStorageDriver(BaseVaultStorageDriver):
    """boto3-backed driver for AWS S3 and S3-compatible stores (OVH, MinIO, …)."""

    def __init__(self, config: dict, *, credential: dict | None = None):
        self._config = config
        #: A sealed credential, already opened by an operator's PIN. Held for
        #: the lifetime of THIS DRIVER only.
        #:
        #: get_bucket_storage() builds a fresh driver per call, so a credential
        #: dies with the driver. The lazy per-instance memoization below is
        #: therefore load-bearing rather than incidental: turning it into a
        #: module-level cache would turn one-action authorization into an
        #: ambient unlock. Do not "optimise" it.
        self._credential = credential or None
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
        if self._credential:
            # Sealed mode: the key came from a RemoteCredential an operator
            # just opened. Never from the environment, never from a column.
            session = boto3.Session(
                aws_access_key_id=self._credential.get("aws_access_key_id"),
                aws_secret_access_key=self._credential.get("aws_secret_access_key"),
                aws_session_token=self._credential.get("session_token") or None,
            )
        elif profile:
            session = boto3.Session(profile_name=profile)
        else:
            # Ambient mode, unchanged: boto3's own chain.
            session = boto3.Session()

        client_kwargs: dict = {}
        if endpoint_url := self._config.get("endpoint_url"):
            # The SSRF chokepoint for every S3 call: read/open/save/exists/
            # delete all reach botocore through this one client. It fires for
            # rows written before the guard existed, which is the point.
            from .outbound import assert_outbound_allowed

            assert_outbound_allowed(endpoint_url, label="S3 endpoint")
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

    def open(self, name: str):
        # botocore's StreamingBody is file-like: FileResponse chunks it
        # without ever holding the object in memory here.
        response = self._get_client().get_object(Bucket=self._bucket_name, Key=name)
        return response["Body"]

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
    """A thin adapter over :class:`toto.vault.peer_client.PeerClient`.

    The wire name IS the remote key: mirror stubs carry the peer's key in
    ``file.name``, so every driver call passes it straight through. The
    previous version of this class spoke an ``/api/vault/buckets/…`` API with
    ``Authorization: Token`` — an API that was never built on any server; the
    peer API (``vault/peer_views.py``) is its real counterpart.
    """

    def __init__(self, peer, *, api_key: str | None = None):
        from .peer_client import PeerClient

        self._client = PeerClient(peer, api_key=api_key)

    def read(self, name: str) -> bytes:
        return self._client.read(name)

    def open(self, name: str):
        return self._client.open_download(name)

    def save(self, name: str, content: bytes) -> str:
        import io

        return self._client.upload(
            io.BytesIO(content), _safe_filename(name))["key"]

    def exists(self, name: str) -> bool:
        return self._client.exists(name)

    def delete(self, name: str) -> None:
        try:
            self._client.delete(name)
        except Exception as exc:  # noqa: BLE001 - a purge must not 500 on a dead peer
            logger.warning("remote_toto delete failed for %r: %s", name, exc)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_bucket_storage(bucket, *, credential: dict | None = None) -> BaseVaultStorageDriver:
    """Return the appropriate storage driver for *bucket*.

    ``credential`` is a plaintext credential an operator's storage PIN has just
    opened (or a queued run redeemed from a capability). Omit it and the driver
    behaves exactly as it always has: boto3's ambient chain for S3, the
    Fernet-sealed peer key for a mount. That is what ``credential_mode
    == "ambient"`` means, and it is the default on every existing row.
    """
    backend = getattr(bucket, "storage_backend", None) or "local"
    config: dict = getattr(bucket, "storage_config", None) or {}

    if backend != "local":
        from toto.vault.models import external_buckets_allowed
        if not external_buckets_allowed():
            # The single driver chokepoint: a lingering non-local bucket row on
            # a local-only host must never reach an external service.
            raise RuntimeError("External buckets are disabled on this host.")

    if backend == "s3":
        merged = dict(config)
        # Fill in provider defaults when the bucket has a linked provider
        # and the config does not already override these values.
        if not merged.get("endpoint_url") and getattr(bucket, "provider_id", None):
            provider = bucket.provider
            region = merged.get("region_name") or provider.default_region
            # account_id reaches the template too — cloudflare_r2's endpoint
            # is https://{account_id}.r2..., and a placeholder left unfilled
            # is not an endpoint.
            endpoint_url = provider.resolve_endpoint_url(
                region=region, account_id=merged.get("account_id", ""))
            if endpoint_url:
                merged["endpoint_url"] = endpoint_url
            if not merged.get("addressing_style"):
                merged["addressing_style"] = provider.addressing_style
            if "use_ssl" not in merged:
                merged["use_ssl"] = provider.use_ssl
        return S3CompatibleVaultStorageDriver(merged, credential=credential)

    if backend == "remote_toto":
        # The peer FK is the whole transport identity — no URL and no secret
        # ever lives in storage_config, so a bucket listing cannot leak either.
        peer = getattr(bucket, "peer", None)
        if peer is None:
            raise RuntimeError(
                f"Bucket '{getattr(bucket, 'slug', '?')}' has no bucket peer "
                "— pair one in the admin and select it on the bucket.")
        if not peer.is_active:
            raise RuntimeError(
                f"Bucket peer '{peer.label}' is deactivated.")
        return RemoteTotoStorageDriver(
            peer, api_key=(credential or {}).get("api_key"))

    return LocalVaultStorageDriver()


# ---------------------------------------------------------------------------
# The seam every byte crosses
# ---------------------------------------------------------------------------
# Views never touch a driver directly: these three are the whole vocabulary,
# so "which disk is this bucket" is answered in exactly one file. Before this
# seam existed the FileField wrote local disk regardless of backend — an s3
# bucket could be copied INTO but never downloaded from.

def read_file_bytes(vault_file) -> bytes:
    """The full content, from wherever the file's bucket keeps it."""
    return get_bucket_storage(vault_file.bucket).read(vault_file.file.name)


def open_file_stream(vault_file):
    """A binary stream for FileResponse — local handle, S3 body, peer wire."""
    return get_bucket_storage(vault_file.bucket).open(vault_file.file.name)


def persist_upload(vault_file, uploaded_file) -> None:
    """Store an upload where its bucket lives, then save the row.

    The one write door for uploads. A LOCAL bucket keeps the FieldFile path
    byte-identical to what the upload doors always did (same upload_to naming,
    same collision suffixing). An S3 bucket goes through the driver and the
    returned key becomes ``file.name``. A remote_toto bucket refuses: rows in
    a mounted remote bucket come from the mirror, and a direct upload here
    would invent a file the origin host never heard of.
    """
    import hashlib

    bucket = vault_file.bucket
    if bucket is None or bucket.is_local:
        vault_file.file.save(uploaded_file.name, uploaded_file, save=True)
        if not vault_file.content_hash:
            vault_file.content_hash = vault_file.create_hash()
            vault_file.save(update_fields=["content_hash"])
        return

    if bucket.storage_backend == "remote_toto":
        raise UploadRefused(
            "This bucket is mounted from another host — files arrive in it "
            "through a Transfer, not a direct upload.")

    size = getattr(uploaded_file, "size", None)
    if size is not None and size > EXTERNAL_UPLOAD_MAX_BYTES:
        raise UploadRefused(
            f"{uploaded_file.name} is larger than the "
            f"{EXTERNAL_UPLOAD_MAX_BYTES // 2**20} MB ceiling for external "
            "storage.")

    content = uploaded_file.read()
    try:
        uploaded_file.seek(0)
    except (OSError, ValueError):
        pass
    key = get_bucket_storage(bucket).save(uploaded_file.name, content)
    vault_file.file = key
    if not vault_file.file_size_bytes:
        vault_file.file_size_bytes = len(content)
    if not vault_file.content_hash:
        vault_file.content_hash = hashlib.sha256(content).hexdigest()
    vault_file.save()
