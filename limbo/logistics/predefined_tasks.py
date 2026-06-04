"""Predefined workflow task for exporting fleet GeoJSON to Vault."""
from __future__ import annotations

import json
import re

from django.utils import timezone
from django.core.files.base import ContentFile

from toto.workflows.predefined_tasks import register

FLEET_EXPORT_SLUG = "logistics-fleet-export"
FLEET_EXPORT_TASK = "fleet_export_geojson"


@register(FLEET_EXPORT_TASK)
def fleet_export_geojson(input_data: dict) -> dict:
    from django.contrib.auth.models import User
    from toto.vault.models import Bucket, VaultDirectory, VaultFile
    from toto.inventory.models import RealWorldObject

    data = input_data.get("data") or input_data
    bucket_id = data["bucket_id"]
    directory_id = data.get("directory_id")
    owner_id = data["owner_id"]
    title = data.get("title") or "Fleet Export"
    category = data.get("category") or None

    qs = (
        RealWorldObject.objects
        .filter(object_type__is_mobile=True, location__geometry__isnull=False)
        .select_related("object_type", "owner", "custodian", "location")
        .prefetch_related("leases")
    )
    if category:
        qs = qs.filter(object_type__category=category)

    features = []
    for obj in qs:
        geom = json.loads(obj.location.geometry.geojson)
        active_lease = next(
            (l for l in obj.leases.all() if l.status == "active"), None
        )
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "id": obj.pk,
                "name": obj.name,
                "object_type": str(obj.object_type) if obj.object_type else "",
                "category": obj.object_type.category if obj.object_type else "",
                "category_display": obj.object_type.get_category_display() if obj.object_type else "",
                "is_mobile": True,
                "owner": str(obj.owner) if obj.owner else None,
                "custodian": str(obj.custodian) if obj.custodian else None,
                "location_label": str(obj.location),
                "lease_status": active_lease.status if active_lease else None,
                "layer": "fleet",
            },
        })

    if not features:
        raise ValueError("No fleet objects with locations found to export.")

    payload = {"type": "FeatureCollection", "features": features}
    content = json.dumps(payload, indent=2).encode()

    bucket = Bucket.objects.get(pk=bucket_id)
    directory = VaultDirectory.objects.get(pk=directory_id, bucket=bucket) if directory_id else None
    owner = User.objects.get(pk=owner_id)

    ts = re.sub(r"[^0-9]", "", timezone.now().isoformat()[:19])
    filename = f"fleet_export_{ts}.geojson"

    password = data.get("password") or None

    vf = VaultFile(
        owner=owner, title=title, bucket=bucket, directory=directory,
        file_type=VaultFile.detect_type("application/geo+json"), is_public=False,
    )
    vf.file.save(filename, ContentFile(content), save=False)
    vf.save()

    if password:
        vf.encrypt(password=password)

    return {"data": {"vault_file_id": vf.pk, "download_url": vf.get_public_url(), "encrypted": bool(password)}}
