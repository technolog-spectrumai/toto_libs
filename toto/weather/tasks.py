from celery import shared_task


@shared_task
def refresh_weather_layers():
    """Trigger workflow runs to refresh all weather map layers from stored observations."""
    from toto.workflows.api import trigger_workflow
    from toto.weather.workflows import LAYER_EXPORT_WEATHER_SLUGS

    results = []
    for slug in LAYER_EXPORT_WEATHER_SLUGS:
        run = trigger_workflow(slug)
        results.append({"slug": slug, "run_id": run.id})
    return results
