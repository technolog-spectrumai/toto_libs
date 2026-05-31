"""Predefined workflow task for exporting detection map layers to Vault."""
from __future__ import annotations

import json
import re

from django.utils import timezone
from django.core.files.base import ContentFile

from toto.workflows.predefined_tasks import register

DETECTIONS_EXPORT_SLUG = "detections-export-layers"
DETECTIONS_EXPORT_TASK = "detections_export_layers"

_ALL_SLUGS = ["detections-severity"]


def _to_geojson(layers: list[dict]) -> tuple[bytes, str, str]:
    features = []
    for layer in layers:
        for poly in layer.get("polygons", []):
            if not poly.get("geometry"):
                continue
            features.append({
                "type": "Feature",
                "geometry": poly["geometry"],
                "properties": {
                    "layer_slug": layer["slug"],
                    "layer_name": layer["name"],
                    "id": poly["id"],
                    "name": poly["name"],
                    "value": poly["value"],
                    "unit": layer.get("unit", ""),
                    **poly.get("properties", {}),
                },
            })
    payload = {"type": "FeatureCollection", "features": features}
    return json.dumps(payload, indent=2).encode(), "application/geo+json", ".geojson"


@register(DETECTIONS_EXPORT_TASK)
def detections_export_layers(input_data: dict) -> dict:
    from django.contrib.auth.models import User
    from toto.vault.models import Bucket, VaultDirectory, VaultFile
    from toto.detections.plugins.location_layer_plugins import detections_severity_layer

    data = input_data.get("data") or input_data
    layer_slugs = data.get("layer_slugs") or _ALL_SLUGS
    bucket_id = data["bucket_id"]
    directory_id = data.get("directory_id")
    owner_id = data["owner_id"]
    title = data.get("title") or "Detections Layers Export"
    password = data.get("password") or None

    _providers = {"detections-severity": detections_severity_layer}
    layers = [r for slug in layer_slugs if slug in _providers for r in [_providers[slug]()] if r]

    if not layers:
        raise ValueError("No layer data to export — no active detections found.")

    bucket = Bucket.objects.get(pk=bucket_id)
    directory = VaultDirectory.objects.get(pk=directory_id, bucket=bucket) if directory_id else None
    owner = User.objects.get(pk=owner_id)

    content, mime_type, ext = _to_geojson(layers)
    ts = re.sub(r"[^0-9]", "", timezone.now().isoformat()[:19])
    filename = f"detections_layers_{ts}{ext}"

    vf = VaultFile(
        owner=owner, title=title, bucket=bucket, directory=directory,
        file_type=VaultFile.detect_type(mime_type), is_public=False,
    )
    vf.file.save(filename, ContentFile(content), save=False)
    vf.save()

    if password:
        vf.encrypt(password=password)

    return {"data": {"vault_file_id": vf.pk, "download_url": vf.get_public_url(), "encrypted": bool(password)}}
