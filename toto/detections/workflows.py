"""
Slugs and lambda code for detections layer-export workflows.

Seeded by the `seed_detections_workflows` management command.
Triggered via:
    from toto.workflows.api import trigger_workflow
    trigger_workflow(LAYER_EXPORT_DETECTIONS_ACTIVE_SLUG)
"""

LAYER_EXPORT_DETECTIONS_ACTIVE_SLUG = "layer-export-detections-active"
LAYER_EXPORT_DETECTIONS_SEVERITY_SLUG = "layer-export-detections-severity"

LAYER_EXPORT_DETECTIONS_SLUGS = [
    LAYER_EXPORT_DETECTIONS_ACTIVE_SLUG,
    LAYER_EXPORT_DETECTIONS_SEVERITY_SLUG,
]

LAYER_EXPORT_DETECTIONS_ACTIVE_LAMBDA = '''
import json
from toto.detections.layer_export import export_active_detections_layer
layer, count = export_active_detections_layer()
print(json.dumps({"data": {"success": True, "layer_slug": layer.slug, "polygon_count": count}}))
'''.strip()

LAYER_EXPORT_DETECTIONS_SEVERITY_LAMBDA = '''
import json
from toto.detections.layer_export import export_detection_severity_layer
layer, count = export_detection_severity_layer()
print(json.dumps({"data": {"success": True, "layer_slug": layer.slug, "polygon_count": count}}))
'''.strip()
