"""
Slugs and lambda code for inventory layer-export workflows.

Seeded by the `seed_inventory_workflows` management command.
Triggered via:
    from toto.workflows.api import trigger_workflow
    trigger_workflow(LAYER_EXPORT_INVENTORY_SITES_SLUG)
"""

LAYER_EXPORT_INVENTORY_SITES_SLUG = "layer-export-inventory-sites"
LAYER_EXPORT_INVENTORY_OBJECTS_SLUG = "layer-export-inventory-objects"

LAYER_EXPORT_INVENTORY_SLUGS = [
    LAYER_EXPORT_INVENTORY_SITES_SLUG,
    LAYER_EXPORT_INVENTORY_OBJECTS_SLUG,
]

LAYER_EXPORT_INVENTORY_SITES_LAMBDA = '''
import json
from toto.inventory.layer_export import export_inventory_sites_layer
layer, count = export_inventory_sites_layer()
print(json.dumps({"data": {"success": True, "layer_slug": layer.slug, "polygon_count": count}}))
'''.strip()

LAYER_EXPORT_INVENTORY_OBJECTS_LAMBDA = '''
import json
from toto.inventory.layer_export import export_inventory_objects_layer
layer, count = export_inventory_objects_layer()
print(json.dumps({"data": {"success": True, "layer_slug": layer.slug, "polygon_count": count}}))
'''.strip()
