from celery import shared_task


@shared_task
def refresh_inventory_layers():
    """Trigger workflow runs to refresh all inventory map layers from live DB state."""
    from toto.workflows.api import trigger_workflow
    from toto.inventory.workflows import LAYER_EXPORT_INVENTORY_SLUGS

    results = []
    for slug in LAYER_EXPORT_INVENTORY_SLUGS:
        run = trigger_workflow(slug)
        results.append({"slug": slug, "run_id": run.id})
    return results
