from django.apps import AppConfig


class DetectionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.detections'
    verbose_name = 'Detections'

    def ready(self):
        from toto.locations.plugins.map_plugins import LocationMapPlugin
        LocationMapPlugin.register(_detection_map_items)

        from toto.detections import predefined_tasks  # noqa: F401 — registers detections workflow tasks


def _detection_map_items():
    import json
    try:
        from toto.detections.models import Detection
        items = []
        for det in Detection.objects.select_related('address', 'zone', 'route', 'category').filter(
            status__in=['new', 'acknowledged', 'handling'],
        ):
            geom = det.map_geometry
            if not geom:
                continue
            items.append({
                'type': 'Detection',
                'id': str(det.pk),
                'name': det.title,
                'label': f'[{det.get_severity_display()}] {det.title}',
                'detection_type': det.detection_type,
                'severity': det.severity,
                'status': det.status,
                'geometry': json.loads(geom.geojson),
            })
        return items
    except Exception:
        return []
