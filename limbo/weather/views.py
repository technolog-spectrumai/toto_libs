import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from toto.celery_utils import celery_available

try:
    from toto.ui import PageProcessor
    _HAS_PAGE_PROCESSOR = True
except ImportError:
    _HAS_PAGE_PROCESSOR = False


def _decorate(context, request):
    if _HAS_PAGE_PROCESSOR:
        return PageProcessor().decorate(context, request)
    return context


@login_required(login_url=reverse_lazy("core:login"))
def weather_index(request):
    from toto.locations.models import Address
    from .models import WeatherSettings

    addresses = list(
        Address.objects.filter(geometry__isnull=False)
        .order_by("locality_name", "street")
    )
    settings_obj = WeatherSettings.get()

    addresses_json = json.dumps([
        {
            "id": a.id,
            "name": str(a),
            "lat": a.geometry.y,
            "lng": a.geometry.x,
        }
        for a in addresses
    ])

    from toto.vault.models import Bucket, VaultDirectory
    buckets = list(Bucket.objects.all().order_by("name"))
    directories = list(VaultDirectory.objects.select_related("bucket").order_by("bucket__name", "name"))
    buckets_json = json.dumps([{"id": b.id, "name": b.name} for b in buckets])
    directories_json = json.dumps([
        {"id": d.id, "bucket_id": d.bucket_id, "path": d.full_path()}
        for d in directories
    ])

    context = _decorate({
        "addresses": addresses,
        "addresses_json": addresses_json,
        "celery_ok": celery_available(),
        "current_provider": settings_obj.get_current_provider_display(),
        "forecast_provider": settings_obj.get_forecast_provider_display(),
        "buckets_json": buckets_json,
        "directories_json": directories_json,
        "export_formats": ["json", "csv", "geojson"],
    }, request)
    return render(request, "weather/index.html", context)


@login_required(login_url=reverse_lazy("core:login"))
@require_POST
def api_refresh_weather(request):
    """Start a current-weather workflow run."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    location_ids = [int(x) for x in (data.get("location_ids") or []) if x]
    if not location_ids:
        return JsonResponse({"error": "Select at least one location."}, status=400)

    if not celery_available():
        return JsonResponse({"error": "No Celery worker running."}, status=503)

    from toto.workflows.api import trigger_workflow
    from toto.workflows.models import Workflow
    from .models import WeatherSettings
    from .workflows import WEATHER_CURRENT_SLUG

    try:
        settings_obj = WeatherSettings.get()
        run = trigger_workflow(WEATHER_CURRENT_SLUG, {
            "location_ids": location_ids,
            "provider": settings_obj.current_provider,
        })
        return JsonResponse({"run_id": run.id})
    except Workflow.DoesNotExist:
        return JsonResponse(
            {"error": "Weather workflow not seeded. Run: manage.py seed_weather_workflows"},
            status=503,
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@login_required(login_url=reverse_lazy("core:login"))
def api_current_data(request):
    """Return the latest weather observations for requested locations."""
    from .models import WeatherObservation

    location_ids = [
        int(x) for x in (
            request.GET.getlist("location_ids[]") or request.GET.getlist("location_ids")
        ) if x
    ]
    if not location_ids:
        return JsonResponse({"observations": {}})

    observations: dict = {}
    for loc_id in location_ids:
        obs = WeatherObservation.objects.filter(address_id=loc_id).first()
        if obs:
            observations[str(loc_id)] = {
                "address_id": loc_id,
                "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
                "loaded_at": obs.loaded_at.isoformat(),
                "temperature": obs.temperature,
                "precipitation_mm": obs.precipitation_mm,
                "precipitation_type": obs.precipitation_type,
                "cloud_cover": obs.cloud_cover,
                "wind_speed_kmh": obs.wind_speed_kmh,
                "wind_direction_deg": obs.wind_direction_deg,
                "visibility_km": obs.visibility_km,
            }

    return JsonResponse({"observations": observations})


@login_required(login_url=reverse_lazy("core:login"))
@require_POST
def api_load_forecast(request):
    """Start a forecast workflow run."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    location_ids = [int(x) for x in (data.get("location_ids") or []) if x]
    start_at = data.get("start_at") or None
    end_at = data.get("end_at") or None
    if not location_ids:
        return JsonResponse({"error": "Select at least one location."}, status=400)

    if not celery_available():
        return JsonResponse({"error": "No Celery worker running."}, status=503)

    from toto.workflows.api import trigger_workflow
    from toto.workflows.models import Workflow
    from .models import WeatherSettings
    from .workflows import WEATHER_FORECAST_SLUG

    try:
        settings_obj = WeatherSettings.get()
        run = trigger_workflow(WEATHER_FORECAST_SLUG, {
            "location_ids": location_ids,
            "provider": settings_obj.forecast_provider,
            "start_at": start_at,
            "end_at": end_at,
        })
        return JsonResponse({"run_id": run.id})
    except Workflow.DoesNotExist:
        return JsonResponse(
            {"error": "Forecast workflow not seeded. Run: manage.py seed_weather_workflows"},
            status=503,
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@login_required(login_url=reverse_lazy("core:login"))
def api_forecast_data(request):
    """Return the latest forecast session data for requested locations."""
    from .models import ForecastSession, ForecastPoint

    location_ids = [
        int(x) for x in (
            request.GET.getlist("location_ids[]") or request.GET.getlist("location_ids")
        ) if x
    ]
    if not location_ids:
        return JsonResponse({"session": None, "points": []})

    session = ForecastSession.objects.filter(
        points__address_id__in=location_ids
    ).order_by("-loaded_at").first()

    if not session:
        return JsonResponse({"session": None, "points": []})

    points = list(
        session.points.filter(address_id__in=location_ids).order_by("valid_at", "address_id")
    )

    return JsonResponse({
        "session": {
            "id": session.id,
            "valid_from": session.valid_from.isoformat(),
            "valid_to": session.valid_to.isoformat(),
            "resolution_hours": session.resolution_hours,
            "loaded_at": session.loaded_at.isoformat(),
        },
        "points": [
            {
                "address_id": p.address_id,
                "valid_at": p.valid_at.isoformat(),
                "temperature": p.temperature,
                "precipitation_mm": p.precipitation_mm,
                "precipitation_type": p.precipitation_type,
                "cloud_cover": p.cloud_cover,
                "wind_speed_kmh": p.wind_speed_kmh,
                "wind_direction_deg": p.wind_direction_deg,
                "visibility_km": p.visibility_km,
            }
            for p in points
        ],
    })


@login_required(login_url=reverse_lazy("core:login"))
def api_run_status(request, run_id):
    """Check a workflow run status (for polling). Also returns download_url for export runs."""
    from toto.workflows.models import WorkflowNodeRun, WorkflowRun

    try:
        run = WorkflowRun.objects.get(pk=run_id)
    except WorkflowRun.DoesNotExist:
        return JsonResponse({"error": "Run not found."}, status=404)

    error = ""
    if run.status == WorkflowRun.FAILED:
        failed_node = run.node_runs.exclude(error="").select_related("node").first()
        if failed_node:
            error = failed_node.error

    download_url = None
    for nr in run.node_runs.filter(status=WorkflowNodeRun.COMPLETED).select_related("node"):
        file_data = ((nr.output_data or {}).get("data") or {})
        if file_data.get("download_url"):
            download_url = file_data["download_url"]
            break

    return JsonResponse({"status": run.status, "error": error, "download_url": download_url})


@login_required(login_url=reverse_lazy("core:login"))
@require_POST
def api_export_layers(request):
    """Trigger a weather layer export workflow run."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    bucket_id = data.get("bucket_id")
    if not bucket_id:
        return JsonResponse({"error": "Select a target bucket."}, status=400)

    layer_slugs = data.get("layer_slugs") or []
    if not layer_slugs:
        return JsonResponse({"error": "Select at least one layer."}, status=400)

    if not celery_available():
        return JsonResponse({"error": "No Celery worker running."}, status=503)

    from toto.workflows.api import trigger_workflow
    from toto.workflows.models import Workflow
    from toto.weather.predefined_tasks import WEATHER_EXPORT_SLUG

    try:
        run = trigger_workflow(WEATHER_EXPORT_SLUG, {
            "data": {
                "layer_slugs": layer_slugs,
                "bucket_id": int(bucket_id),
                "directory_id": int(data["directory_id"]) if data.get("directory_id") else None,
                "title": data.get("title", "").strip() or None,
                "password": data.get("password") or None,
                "owner_id": request.user.pk,
            }
        })
        return JsonResponse({"run_id": run.id})
    except Workflow.DoesNotExist:
        return JsonResponse(
            {"error": "Export workflow not seeded. Run: manage.py seed_weather_workflows"},
            status=503,
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)

