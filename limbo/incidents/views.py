import json

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView, TemplateView

from toto.core.page import PageProcessor

from .models import Incident, IncidentCategory


def _parent_template():
    from django.apps import apps
    return "tactical/base.html" if apps.is_installed("toto.tactical") else "incidents/base_standalone.html"


def _incident_map_feature(incident):
    from toto.locations.views import geometry_json
    geom = incident.map_geometry
    if not geom:
        return None
    return {
        "id": str(incident.pk),
        "title": incident.title,
        "type": incident.get_incident_type_display(),
        "severity": incident.severity,
        "status": incident.get_status_display(),
        "location": incident.location_label or "",
        "url": incident.get_absolute_url(),
        "geometry": geometry_json(geom),
    }


class IncidentsContextMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["incidents_parent_template"] = _parent_template()
        return PageProcessor().decorate(context, self.request)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

class IncidentListView(IncidentsContextMixin, LoginRequiredMixin, ListView):
    model = Incident
    template_name = "incidents/incident_list.html"
    context_object_name = "incidents"
    paginate_by = 40

    def get_queryset(self):
        qs = Incident.objects.select_related("address", "zone", "category").order_by("-created_at")
        status = self.request.GET.get("status")
        severity = self.request.GET.get("severity")
        if status:
            qs = qs.filter(status=status)
        if severity:
            qs = qs.filter(severity=severity)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_choices"] = Incident.STATUS_CHOICES
        context["severity_choices"] = Incident.SEVERITY_CHOICES
        context["active_status"] = self.request.GET.get("status", "")
        context["active_severity"] = self.request.GET.get("severity", "")
        context["incident_features"] = [
            f for f in (_incident_map_feature(inc) for inc in context["incidents"]) if f
        ]
        return context


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class IncidentDetailView(IncidentsContextMixin, LoginRequiredMixin, DetailView):
    model = Incident
    template_name = "incidents/incident_detail.html"
    context_object_name = "incident"

    def get_queryset(self):
        return Incident.objects.select_related(
            "address", "zone", "category", "reported_by",
            "confirmed_by", "source_detection",
        )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class IncidentDashboardView(IncidentsContextMixin, LoginRequiredMixin, TemplateView):
    template_name = "incidents/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        open_qs = Incident.objects.filter(status__in=["open", "contained"])
        status_counts = list(Incident.objects.values("status").annotate(count=Count("id")).order_by("status"))
        severity_counts = list(Incident.objects.values("severity").annotate(count=Count("id")).order_by("severity"))
        recent = open_qs.select_related("address", "zone", "category").order_by("-created_at")[:20]
        context.update({
            "open_count": open_qs.count(),
            "critical_count": open_qs.filter(severity="critical").count(),
            "total_count": Incident.objects.count(),
            "recent_incidents": recent,
            "status_chart_data": {
                "labels": [dict(Incident.STATUS_CHOICES).get(r["status"], r["status"]) for r in status_counts],
                "data": [r["count"] for r in status_counts],
            },
            "severity_chart_data": {
                "labels": [dict(Incident.SEVERITY_CHOICES).get(r["severity"], r["severity"]) for r in severity_counts],
                "data": [r["count"] for r in severity_counts],
            },
        })
        return context


# ---------------------------------------------------------------------------
# Create (manual)
# ---------------------------------------------------------------------------

@login_required
def incident_create(request):
    from toto.locations.models import Address, Zone
    context = {
        "incidents_parent_template": _parent_template(),
        "severity_choices": Incident.SEVERITY_CHOICES,
        "type_choices": Incident.TYPE_CHOICES,
        "categories": IncidentCategory.objects.filter(is_active=True).order_by("name"),
        "addresses": Address.objects.order_by("locality_name", "street"),
        "zones": Zone.objects.order_by("name"),
    }
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        if not title:
            context["error"] = "Title is required."
            return render(request, "incidents/incident_create.html", PageProcessor().decorate(context, request))
        inc = Incident.objects.create(
            title=title,
            description=request.POST.get("description", "").strip(),
            severity=request.POST.get("severity", "medium"),
            incident_type=request.POST.get("incident_type", "other"),
            status="open",
            confirmed_by=request.user,
            confirmed_at=timezone.now(),
        )
        cat_id = request.POST.get("category")
        if cat_id:
            inc.category_id = cat_id
        addr_id = request.POST.get("address")
        if addr_id:
            inc.address_id = addr_id
        zone_id = request.POST.get("zone")
        if zone_id:
            inc.zone_id = zone_id
        inc.save()
        return redirect("incidents:incident-detail", pk=inc.pk)
    return render(request, "incidents/incident_create.html", PageProcessor().decorate(context, request))


# ---------------------------------------------------------------------------
# Status update
# ---------------------------------------------------------------------------

@login_required
@require_POST
def api_update_status(request, pk):
    incident = get_object_or_404(Incident, pk=pk)
    status = request.POST.get("status")
    valid = {c[0] for c in Incident.STATUS_CHOICES}
    if status not in valid:
        return JsonResponse({"error": f"Invalid status: {status}"}, status=400)
    incident.status = status
    if status in ("resolved", "closed") and not incident.end_time:
        incident.end_time = timezone.now()
    incident.save(update_fields=["status", "end_time", "updated_at"])
    return JsonResponse({"status": incident.status, "status_display": incident.get_status_display()})


# ---------------------------------------------------------------------------
# GeoJSON import
# ---------------------------------------------------------------------------

_DETECTION_TYPE_TO_INCIDENT_TYPE = {
    "incident": "emergency",
    "observation": "other",
    "anomaly": "other",
    "hazard": "hazard",
    "other": "other",
}

_VALID_INCIDENT_TYPES = {c[0] for c in Incident.TYPE_CHOICES}
_VALID_SEVERITIES = {c[0] for c in Incident.SEVERITY_CHOICES}


@login_required
@require_POST
def api_import_incidents(request):
    uploaded = request.FILES.get("file")
    if not uploaded:
        return JsonResponse({"error": "No file uploaded."}, status=400)

    try:
        data = json.loads(uploaded.read().decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "Invalid GeoJSON — could not parse file."}, status=400)

    if data.get("type") != "FeatureCollection":
        return JsonResponse({"error": "Expected a GeoJSON FeatureCollection."}, status=400)

    features = data.get("features") or []
    if not features:
        return JsonResponse({"error": "File contains no features."}, status=400)

    from django.contrib.gis.geos import GEOSGeometry
    from toto.locations.models import Address

    created_count = 0
    skipped = 0
    errors = []

    for feat in features:
        props = feat.get("properties") or {}
        title = (props.get("name") or props.get("title") or "").strip()
        if not title:
            skipped += 1
            continue

        raw_severity = props.get("severity", "medium")
        severity = raw_severity if raw_severity in _VALID_SEVERITIES else "medium"

        raw_type = props.get("detection_type") or props.get("incident_type") or "other"
        incident_type = (
            _DETECTION_TYPE_TO_INCIDENT_TYPE.get(raw_type, raw_type)
            if raw_type not in _VALID_INCIDENT_TYPES
            else raw_type
        )
        if incident_type not in _VALID_INCIDENT_TYPES:
            incident_type = "other"

        # Geometry: take centroid if polygon/multipolygon, keep point as-is
        geom_data = feat.get("geometry")
        point_geom = None
        if geom_data:
            try:
                g = GEOSGeometry(json.dumps(geom_data), srid=4326)
                if g.geom_type == "Point":
                    point_geom = g
                else:
                    point_geom = g.centroid
            except Exception:
                pass

        # Try to match to an existing address by geometry proximity
        address = None
        if point_geom:
            address = (
                Address.objects
                .filter(geometry__distance_lte=(point_geom, 0.001))  # ~100m
                .order_by("geometry")
                .first()
            )

        extra = {k: v for k, v in props.items() if k not in {"name", "title", "severity", "detection_type", "incident_type", "color", "layer"}}

        try:
            inc = Incident.objects.create(
                title=title,
                severity=severity,
                incident_type=incident_type,
                status="open",
                confirmed_by=request.user,
                confirmed_at=timezone.now(),
                address=address,
                geometry=point_geom if not address else None,
                properties=extra,
            )
            created_count += 1
        except Exception as exc:
            errors.append(f"{title}: {exc}")

    return JsonResponse({
        "created": created_count,
        "skipped": skipped,
        "errors": errors[:10],
    })


# ---------------------------------------------------------------------------
# Promote detection → incident
# ---------------------------------------------------------------------------

@login_required
@require_POST
def api_promote_detection(request, detection_pk):
    from toto.detections.models import Detection

    detection = get_object_or_404(Detection, pk=detection_pk)

    # If already promoted, return existing incident
    if hasattr(detection, "promoted_incident") and detection.promoted_incident:
        return JsonResponse({
            "incident_pk": str(detection.promoted_incident.pk),
            "already_existed": True,
        })

    incident_type = _DETECTION_TYPE_TO_INCIDENT_TYPE.get(detection.detection_type, "other")
    if incident_type not in _VALID_INCIDENT_TYPES:
        incident_type = "other"

    inc = Incident.objects.create(
        title=detection.title,
        description=getattr(detection, "description", ""),
        severity=detection.severity,
        incident_type=incident_type,
        status="open",
        address=detection.address,
        zone=detection.zone,
        reported_by=detection.reported_by,
        source_detection=detection,
        confirmed_by=request.user,
        confirmed_at=timezone.now(),
        start_time=detection.start_time,
    )

    return JsonResponse({
        "incident_pk": str(inc.pk),
        "already_existed": False,
    })
