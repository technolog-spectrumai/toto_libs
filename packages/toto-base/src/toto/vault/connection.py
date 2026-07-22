"""
BucketConnectionSpec — a URL-serialisable value object that encodes
all non-secret configuration needed to reach a Bucket's storage backend.

Credentials are NEVER included — they come from environment variables.

URL schemes
-----------
  local:///bucket-slug
  s3+aws://bucket-name/vault/
  s3+ovh://bucket-name@s3.sbg.io.cloud.ovh.net/vault/
  s3+minio://bucket-name@minio.example.com/vault/
  toto://other-server.example.com/vault/buckets/bucket-slug/
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse


@dataclass(frozen=True)
class BucketConnectionSpec:
    backend: str        # "local" | "s3" | "remote_toto"
    bucket_name: str    # S3 bucket name, local slug, or remote bucket slug
    provider: str = ""  # "aws" | "ovh" | "minio" | …  (s3 only)
    endpoint_url: str = ""   # resolved endpoint (s3 only)
    region: str = ""
    prefix: str = "vault/"
    server_url: str = ""     # remote_toto only

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_url(self) -> str:
        if self.backend == "local":
            return f"local:///{self.bucket_name}"

        if self.backend == "remote_toto":
            host = self.server_url.removeprefix("https://").removeprefix("http://")
            return f"toto://{host}/vault/buckets/{self.bucket_name}/"

        # s3 / s3+<provider>
        scheme = f"s3+{self.provider}" if self.provider else "s3"
        if self.endpoint_url:
            host = self.endpoint_url.removeprefix("https://").removeprefix("http://")
            netloc = f"{self.bucket_name}@{host}"
        else:
            netloc = self.bucket_name
        path = "/" + self.prefix.strip("/")
        return urlunparse((scheme, netloc, path, "", "", ""))

    # ------------------------------------------------------------------
    # Deserialisation
    # ------------------------------------------------------------------

    @classmethod
    def from_url(cls, url: str) -> BucketConnectionSpec:
        parsed = urlparse(url)
        scheme = parsed.scheme  # "local", "s3", "s3+ovh", "toto"

        if scheme == "local":
            return cls(backend="local", bucket_name=parsed.path.lstrip("/"))

        if scheme == "toto":
            server_url = f"https://{parsed.netloc}"
            # path: /vault/buckets/<slug>/
            parts = [p for p in parsed.path.split("/") if p]
            bucket_slug = parts[-1] if parts else ""
            return cls(backend="remote_toto", bucket_name=bucket_slug, server_url=server_url)

        if scheme == "s3" or scheme.startswith("s3+"):
            provider = scheme[3:].lstrip("+")  # "" for bare "s3", "ovh" for "s3+ovh"
            netloc = parsed.netloc
            if "@" in netloc:
                bucket_name, endpoint_host = netloc.rsplit("@", 1)
                endpoint_url = f"https://{endpoint_host}"
            else:
                bucket_name = netloc
                endpoint_url = ""
            prefix = parsed.path.strip("/") + "/" if parsed.path.strip("/") else "vault/"
            return cls(
                backend="s3",
                bucket_name=bucket_name,
                provider=provider,
                endpoint_url=endpoint_url,
                prefix=prefix,
            )

        raise ValueError(f"Unrecognised connection URL scheme: {scheme!r}")

    # ------------------------------------------------------------------
    # Build from DB model
    # ------------------------------------------------------------------

    @classmethod
    def from_bucket(cls, bucket) -> BucketConnectionSpec:
        from toto.vault.models import StorageBackend

        backend = bucket.storage_backend
        cfg: dict = bucket.storage_config or {}

        if backend == StorageBackend.LOCAL:
            return cls(backend="local", bucket_name=bucket.slug)

        if backend == StorageBackend.REMOTE_TOTO:
            return cls(
                backend="remote_toto",
                bucket_name=cfg.get("bucket_slug", bucket.slug),
                server_url=cfg.get("server_url", ""),
            )

        # S3-compatible
        provider_name = ""
        endpoint_url = cfg.get("endpoint_url", "")

        if bucket.provider_id:
            provider_name = bucket.provider.name
            if not endpoint_url:
                region = cfg.get("region_name", "") or bucket.provider.default_region
                endpoint_url = bucket.provider.resolve_endpoint_url(region=region)

        return cls(
            backend="s3",
            bucket_name=cfg.get("bucket_name", ""),
            provider=provider_name,
            endpoint_url=endpoint_url,
            region=cfg.get("region_name", ""),
            prefix=cfg.get("prefix", "vault/"),
        )

    # ------------------------------------------------------------------
    # Convert back to storage_config dict (for model import)
    # ------------------------------------------------------------------

    def to_storage_config(self) -> dict:
        if self.backend == "local":
            return {}
        if self.backend == "remote_toto":
            return {"server_url": self.server_url, "bucket_slug": self.bucket_name}
        config: dict = {"bucket_name": self.bucket_name, "prefix": self.prefix}
        if self.endpoint_url:
            config["endpoint_url"] = self.endpoint_url
        if self.region:
            config["region_name"] = self.region
        return config
