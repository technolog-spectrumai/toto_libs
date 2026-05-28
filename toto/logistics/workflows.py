"""
Slugs and lambda code for logistics layer-export workflows.

Seeded by the `seed_logistics_workflows` management command.
Triggered via:
    from toto.workflows.api import trigger_workflow
    trigger_workflow(LAYER_EXPORT_LOGISTICS_ACTIVE_PACKAGES_SLUG)
"""

LAYER_EXPORT_LOGISTICS_ACTIVE_PACKAGES_SLUG = "layer-export-logistics-active-packages"
LAYER_EXPORT_LOGISTICS_TRANSPORT_DENSITY_SLUG = "layer-export-logistics-transport-density"

LAYER_EXPORT_LOGISTICS_SLUGS = [
    LAYER_EXPORT_LOGISTICS_ACTIVE_PACKAGES_SLUG,
    LAYER_EXPORT_LOGISTICS_TRANSPORT_DENSITY_SLUG,
]

LAYER_EXPORT_LOGISTICS_ACTIVE_PACKAGES_LAMBDA = '''
import json
from toto.logistics.layer_export import export_active_packages_layer
layer, count = export_active_packages_layer()
print(json.dumps({"data": {"success": True, "layer_slug": layer.slug, "polygon_count": count}}))
'''.strip()

LAYER_EXPORT_LOGISTICS_TRANSPORT_DENSITY_LAMBDA = '''
import json
from toto.logistics.layer_export import export_transport_density_layer
layer, count = export_transport_density_layer()
print(json.dumps({"data": {"success": True, "layer_slug": layer.slug, "polygon_count": count}}))
'''.strip()
