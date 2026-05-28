from celery import shared_task


@shared_task
def refresh_detections_layers():
    """Trigger workflow runs to refresh all detection map layers from live DB state."""
    from toto.workflows.api import trigger_workflow
    from toto.detections.workflows import LAYER_EXPORT_DETECTIONS_SLUGS

    results = []
    for slug in LAYER_EXPORT_DETECTIONS_SLUGS:
        run = trigger_workflow(slug)
        results.append({"slug": slug, "run_id": run.id})
    return results
