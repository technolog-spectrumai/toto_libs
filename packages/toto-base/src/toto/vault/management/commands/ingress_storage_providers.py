"""
Seed StorageProvider records.

Non-full run (default): seeds AWS S3 and OVH Object Storage only.
Full run (--full):       seeds all built-in providers.

All seeds are idempotent (update_or_create on name).
"""
from toto.ingress import IngressCommand
from toto.vault.models import StorageProvider


# ── Provider definitions ──────────────────────────────────────────────────────
# Each entry: (name, display_name, endpoint_url_template, default_region,
#              addressing_style, use_ssl)

_CORE_PROVIDERS = [
    (
        "aws",
        "Amazon S3",
        "",                    # AWS uses default routing — no endpoint needed
        "us-east-1",
        "virtual",
        True,
    ),
    (
        "ovh",
        "OVH Object Storage (S3)",
        "https://s3.{region}.io.cloud.ovh.net",
        "sbg",
        "path",
        True,
    ),
]

_EXTRA_PROVIDERS = [
    (
        "minio",
        "MinIO",
        "http://localhost:9000",
        "",
        "path",
        False,
    ),
    (
        "digitalocean",
        "DigitalOcean Spaces",
        "https://{region}.digitaloceanspaces.com",
        "nyc3",
        "virtual",
        True,
    ),
    (
        "backblaze_b2",
        "Backblaze B2",
        "https://s3.{region}.backblazeb2.com",
        "us-west-004",
        "path",
        True,
    ),
    (
        "cloudflare_r2",
        "Cloudflare R2",
        "https://{account_id}.r2.cloudflarestorage.com",
        "auto",
        "path",
        True,
    ),
]


class Command(IngressCommand):
    help = "Seed built-in StorageProvider records. Non-full: AWS + OVH. Full: all providers."

    def process(self):
        providers = list(_CORE_PROVIDERS)
        if self.full:
            providers += _EXTRA_PROVIDERS

        for name, display_name, endpoint_url_template, default_region, addressing_style, use_ssl in providers:
            _, created = StorageProvider.objects.update_or_create(
                name=name,
                defaults={
                    "display_name": display_name,
                    "endpoint_url_template": endpoint_url_template,
                    "default_region": default_region,
                    "addressing_style": addressing_style,
                    "use_ssl": use_ssl,
                    "is_builtin": True,
                },
            )
            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"  {verb}: {display_name}"))

        total = StorageProvider.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f"Storage providers ready ({total} total in DB)."
        ))
