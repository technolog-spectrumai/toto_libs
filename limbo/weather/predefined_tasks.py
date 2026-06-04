"""Predefined workflow task for exporting weather map layers to Vault."""
from __future__ import annotations

import json
import re

from django.utils import timezone
from django.core.files.base import ContentFile

from toto.workflows.predefined_tasks import register

WEATHER_EXPORT_SLUG = "weather-export-layers"
WEATHER_EXPORT_TASK = "weather_export_layers"

_ALL_SLUGS = ["weather-temperature", "weather-precipitation"]


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


@register(WEATHER_EXPORT_TASK)
def weather_export_layers(input_data: dict) -> dict:
    from django.contrib.auth.models import User
    from toto.vault.models import Bucket, VaultDirectory, VaultFile
    from toto.weather.plugins.location_layer_plugins import (
        weather_precipitation_layer,
        weather_temperature_layer,
    )

    data = input_data.get("data") or input_data
    layer_slugs = data.get("layer_slugs") or _ALL_SLUGS
    bucket_id = data["bucket_id"]
    directory_id = data.get("directory_id")
    owner_id = data["owner_id"]
    title = data.get("title") or "Weather Layers Export"
    password = data.get("password") or None

    _providers = {
        "weather-temperature": weather_temperature_layer,
        "weather-precipitation": weather_precipitation_layer,
    }
    layers = [r for slug in layer_slugs if slug in _providers for r in [_providers[slug]()] if r]

    if not layers:
        raise ValueError("No layer data to export — no weather observations found.")

    bucket = Bucket.objects.get(pk=bucket_id)
    directory = VaultDirectory.objects.get(pk=directory_id, bucket=bucket) if directory_id else None
    owner = User.objects.get(pk=owner_id)

    content, mime_type, ext = _to_geojson(layers)
    ts = re.sub(r"[^0-9]", "", timezone.now().isoformat()[:19])
    filename = f"weather_layers_{ts}{ext}"

    vf = VaultFile(
        owner=owner, title=title, bucket=bucket, directory=directory,
        file_type=VaultFile.detect_type(mime_type), is_public=False,
    )
    vf.file.save(filename, ContentFile(content), save=False)
    vf.save()

    if password:
        vf.encrypt(password=password)

    return {"data": {"vault_file_id": vf.pk, "download_url": vf.get_public_url(), "encrypted": bool(password)}}
