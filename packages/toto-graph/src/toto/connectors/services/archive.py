"""Raw payload archiving — every run's fetched pages land in the vault.

One JSON VaultFile per run, in a shared ``connectors-archive`` bucket with a
per-connector directory, so the exact upstream data behind any graph change
stays inspectable long after the run.
"""

import hashlib
import json

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.utils import timezone

ARCHIVE_BUCKET_SLUG = "connectors-archive"
ARCHIVE_BUCKET_NAME = "Connectors Archive"


def _fallback_owner():
    User = get_user_model()
    return User.objects.filter(is_superuser=True).order_by("pk").first()


def ensure_archive_bucket(owner=None):
    from toto.vault.models import Bucket

    bucket = Bucket.objects.filter(slug=ARCHIVE_BUCKET_SLUG).first()
    if bucket:
        return bucket
    owner = owner or _fallback_owner()
    if owner is None:
        raise RuntimeError(
            "Cannot create the connectors archive bucket: no owner available "
            "(pass one or create a superuser)."
        )
    return Bucket.objects.create(
        slug=ARCHIVE_BUCKET_SLUG, name=ARCHIVE_BUCKET_NAME, owner=owner
    )


def ensure_connector_directory(bucket, connector):
    from toto.vault.models import VaultDirectory

    directory, _created = VaultDirectory.objects.get_or_create(
        bucket=bucket,
        parent=None,
        name=connector.slug,
        defaults={"owner": connector.owner or bucket.owner},
    )
    return directory


def archive_payload(run, result):
    """Write the run's fetched pages as a JSON VaultFile; returns the file."""
    from toto.vault.models import VaultFile

    connector = run.connector
    owner = run.created_by or connector.owner or _fallback_owner()
    bucket = ensure_archive_bucket(owner)
    directory = ensure_connector_directory(bucket, connector)

    fetched_at = timezone.now()
    envelope = {
        "connector": connector.slug,
        "run_id": run.pk,
        "fetched_at": fetched_at.isoformat(),
        "stats": result.stats,
        "pages": [
            {**page.audit_entry(), "json": page.json}
            for page in result.pages
        ],
    }
    content = json.dumps(envelope, ensure_ascii=False, default=str).encode("utf-8")

    vault_file = VaultFile(
        owner=owner,
        title=f"{connector.slug} run #{run.pk} raw payload",
        bucket=bucket,
        directory=directory,
        file_type="json",
        is_public=False,
    )
    # Hash the bytes already in memory — VaultFile.create_hash would re-read
    # the whole payload back out of storage for the same result.
    vault_file.content_hash = hashlib.sha256(content).hexdigest()
    filename = f"{connector.slug}_run_{run.pk}_{fetched_at.strftime('%Y%m%dT%H%M%S')}.json"
    vault_file.file.save(filename, ContentFile(content), save=False)
    vault_file.save()
    return vault_file
