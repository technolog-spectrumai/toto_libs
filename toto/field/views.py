import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Avg, Case, Count, FloatField, Value, When
from django.http import JsonResponse
from django.utils import timezone
from django.views.generic import TemplateView

from toto.core.page import PageProcessor


def _page(context, request):
    return PageProcessor().decorate(context, request)


# ---------------------------------------------------------------------------
# Severity score helper (shared by command + metrics)
# ---------------------------------------------------------------------------

_SEVERITY_SCORE = Case(
    When(severity="low", then=Value(1.0)),
    When(severity="medium", then=Value(2.0)),
    When(severity="high", then=Value(3.0)),
    When(severity="critical", then=Value(4.0)),
    default=Value(1.0),
    output_field=FloatField(),
)

_ACTIVE_DETECTION_STATUSES = ["new", "acknowledged", "handling"]


# ---------------------------------------------------------------------------
# Command — map + data grid
# ---------------------------------------------------------------------------

class CommandView(LoginRequiredMixin, TemplateView):
    template_name = "field/command.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from toto.locations.models import Address, Territory, Zone, Route, MapLayer
        from toto.detections.models import Detection
        from toto.logistics.models import Package, Transport, PackageStatus
        from toto.inventory.models import InventorySite

        active_statuses = ["new", "acknowledged", "handling"]
        in_transit = [PackageStatus.PICKED_UP, PackageStatus.IN_TRANSIT,
                      PackageStatus.AT_HUB, PackageStatus.OUT_FOR_DELIVERY]

        context.update(_page({
            "map_data_url": "/field/api/map/",
            # summary ribbon
            "active_detections": Detection.objects.filter(status__in=active_statuses).count(),
            "active_transports": Transport.objects.filter(is_active=True).count(),
            "packages_in_transit": Package.objects.filter(status__in=in_transit).count(),
            "inventory_sites": InventorySite.objects.filter(is_active=True).count(),
            # grid panels
            "recent_detections": Detection.objects.filter(
                status__in=active_statuses
            ).select_related("address", "zone", "category").order_by("-start_time")[:20],
            "active_transport_list": Transport.objects.filter(
                is_active=True
            ).select_related("current_location", "origin", "destination")[:20],
            "inventory_site_list": InventorySite.objects.filter(
                is_active=True
            ).select_related("address")[:20],
        }, self.request))
        return context


# ---------------------------------------------------------------------------
# Map data API — GeoJSON for all layers
# ---------------------------------------------------------------------------

class MapDataView(LoginRequiredMixin, TemplateView):

    def get(self, request, *args, **kwargs):
        from toto.locations.models import Address, Territory, Zone, Route, MapLayer, MapLayerPolygon
        from toto.detections.models import Detection
        from toto.logistics.models import Transport, PackageStatus, Package
        from toto.inventory.models import InventorySite
        from toto.weather.models import WeatherObservation
        from django.db.models import Subquery, OuterRef

        features = []

        # ── Locations: territories ───────────────────────────────────────
        for t in Territory.objects.exclude(geometry=None):
            features.append({
                "type": "Feature",
                "geometry": json.loads(t.geometry.geojson),
                "properties": {"layer": "territory", "name": t.name, "id": t.pk},
            })

        # ── Locations: zones ─────────────────────────────────────────────
        for z in Zone.objects.exclude(geometry=None).select_related("territory"):
            features.append({
                "type": "Feature",
                "geometry": json.loads(z.geometry.geojson),
                "properties": {
                    "layer": "zone", "name": z.name, "id": z.pk,
                    "territory": z.territory.name if z.territory else "",
                },
            })

        # ── Locations: routes ────────────────────────────────────────────
        for r in Route.objects.exclude(geometry=None):
            features.append({
                "type": "Feature",
                "geometry": json.loads(r.geometry.geojson),
                "properties": {"layer": "route", "name": str(r), "id": r.pk},
            })

        # ── Locations: addresses ─────────────────────────────────────────
        for a in Address.objects.exclude(geometry=None):
            features.append({
                "type": "Feature",
                "geometry": json.loads(a.geometry.geojson),
                "properties": {"layer": "address", "name": str(a), "id": a.pk},
            })

        # ── Locations: map layers (existing heatmap polygons) ────────────
        for poly in MapLayerPolygon.objects.select_related("layer").filter(layer__is_active=True):
            features.append({
                "type": "Feature",
                "geometry": json.loads(poly.geometry.geojson),
                "properties": {
                    "layer": "map_layer",
                    "layer_name": poly.layer.name,
                    "layer_slug": poly.layer.slug,
                    "name": poly.name,
                    "value": poly.value,
                    "unit": poly.layer.unit,
                },
            })

        # ── Detections ───────────────────────────────────────────────────
        severity_color = {"low": "#22c55e", "medium": "#f59e0b", "high": "#ef4444", "critical": "#7c3aed"}
        for d in Detection.objects.filter(
            status__in=_ACTIVE_DETECTION_STATUSES
        ).select_related("address", "zone", "route", "category"):
            geom = None
            if d.address and d.address.geometry:
                geom = json.loads(d.address.geometry.geojson)
            elif d.zone and d.zone.geometry:
                geom = json.loads(d.zone.geometry.geojson)
            elif d.route and d.route.geometry:
                geom = json.loads(d.route.geometry.geojson)
            if geom:
                features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "layer": "detection",
                        "name": getattr(d, "title", str(d)),
                        "severity": d.severity,
                        "status": d.status,
                        "color": severity_color.get(d.severity, "#94a3b8"),
                        "id": str(d.pk),
                    },
                })

        # ── Logistics: active transports ─────────────────────────────────
        for t in Transport.objects.filter(
            is_active=True, current_location__geometry__isnull=False
        ).select_related("current_location"):
            features.append({
                "type": "Feature",
                "geometry": json.loads(t.current_location.geometry.geojson),
                "properties": {
                    "layer": "transport",
                    "name": str(t),
                    "mode": t.mode,
                    "id": t.pk,
                },
            })

        # ── Inventory: active sites ──────────────────────────────────────
        for s in InventorySite.objects.filter(
            is_active=True, address__geometry__isnull=False
        ).select_related("address"):
            features.append({
                "type": "Feature",
                "geometry": json.loads(s.address.geometry.geojson),
                "properties": {
                    "layer": "inventory_site",
                    "name": s.name,
                    "site_type": s.site_type,
                    "id": s.pk,
                },
            })

        # ── Weather: latest observation per address ──────────────────────
        latest_obs_id = (
            WeatherObservation.objects
            .filter(address=OuterRef("pk"), temperature__isnull=False)
            .order_by("-loaded_at")
            .values("id")[:1]
        )
        for a in Address.objects.exclude(geometry=None).annotate(obs_id=Subquery(latest_obs_id)).filter(obs_id__isnull=False):
            try:
                obs = WeatherObservation.objects.get(pk=a.obs_id)
                features.append({
                    "type": "Feature",
                    "geometry": json.loads(a.geometry.geojson),
                    "properties": {
                        "layer": "weather",
                        "name": str(a),
                        "temperature": obs.temperature,
                        "wind_speed_kmh": obs.wind_speed_kmh,
                        "cloud_cover": obs.cloud_cover,
                        "precipitation_mm": obs.precipitation_mm,
                        "id": a.pk,
                    },
                })
            except WeatherObservation.DoesNotExist:
                pass

        return JsonResponse({"type": "FeatureCollection", "features": features})


# ---------------------------------------------------------------------------
# Feed — recent activity across all apps
# ---------------------------------------------------------------------------

class FeedView(LoginRequiredMixin, TemplateView):
    template_name = "field/feed.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from toto.detections.models import Detection
        from toto.logistics.models import PackageEvent
        from toto.weather.models import WeatherObservation
        from toto.inventory.models import RealWorldObject

        # Gather events from each source, tag with type, sort by time
        events = []

        for d in Detection.objects.select_related("address", "zone", "category", "reported_by").order_by("-created_at")[:30]:
            events.append({
                "type": "detection",
                "icon": "fa-triangle-exclamation",
                "color": {"low": "success", "medium": "caution", "high": "warn", "critical": "warn"}.get(d.severity, "caution"),
                "title": getattr(d, "title", f"Detection #{d.pk}"),
                "subtitle": f"{d.get_severity_display()} · {d.get_status_display()} · {d.location_label or '—'}",
                "time": d.created_at,
                "url": d.get_absolute_url(),
            })

        for e in PackageEvent.objects.select_related("package", "location", "transport").order_by("-occurred_at")[:30]:
            events.append({
                "type": "package",
                "icon": "fa-box",
                "color": "accent",
                "title": f"Package {e.package.tracking_number}",
                "subtitle": f"{e.get_status_display()} · {e.location or e.transport or '—'}",
                "time": e.occurred_at,
                "url": None,
            })

        for obs in WeatherObservation.objects.select_related("address").order_by("-loaded_at")[:20]:
            events.append({
                "type": "weather",
                "icon": "fa-cloud-sun",
                "color": "link",
                "title": str(obs.address),
                "subtitle": f"{obs.temperature}°C · {obs.wind_speed_kmh} km/h wind" if obs.temperature else "Observation recorded",
                "time": obs.loaded_at,
                "url": None,
            })

        events.sort(key=lambda e: e["time"], reverse=True)

        context.update(_page({
            "events": events[:80],
        }, self.request))
        return context


# ---------------------------------------------------------------------------
# Metrics — generals overview
# ---------------------------------------------------------------------------

class MetricsView(LoginRequiredMixin, TemplateView):
    template_name = "field/metrics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from toto.detections.models import Detection
        from toto.logistics.models import Package, Transport, PackageStatus
        from toto.inventory.models import InventorySite, RealWorldObject
        from toto.weather.models import WeatherObservation
        from toto.locations.models import Address, Territory, Zone, Route

        in_transit = [PackageStatus.PICKED_UP, PackageStatus.IN_TRANSIT,
                      PackageStatus.AT_HUB, PackageStatus.OUT_FOR_DELIVERY]

        # Detection breakdown
        det_by_severity = {
            row["severity"]: row["count"]
            for row in Detection.objects.filter(
                status__in=_ACTIVE_DETECTION_STATUSES
            ).values("severity").annotate(count=Count("id"))
        }
        det_by_status = {
            row["status"]: row["count"]
            for row in Detection.objects.values("status").annotate(count=Count("id"))
        }

        # Package breakdown
        pkg_by_status = {
            row["status"]: row["count"]
            for row in Package.objects.values("status").annotate(count=Count("id"))
        }

        context.update(_page({
            # ── headline KPIs ──────────────────────────────────────────
            "kpi": {
                "active_detections": Detection.objects.filter(status__in=_ACTIVE_DETECTION_STATUSES).count(),
                "critical_detections": det_by_severity.get("critical", 0),
                "high_detections": det_by_severity.get("high", 0),
                "packages_in_transit": Package.objects.filter(status__in=in_transit).count(),
                "packages_failed": pkg_by_status.get("failed", 0),
                "active_transports": Transport.objects.filter(is_active=True).count(),
                "inventory_sites": InventorySite.objects.filter(is_active=True).count(),
                "inventory_objects": RealWorldObject.objects.count(),
                "addresses_with_weather": WeatherObservation.objects.values("address").distinct().count(),
                "total_addresses": Address.objects.count(),
                "territories": Territory.objects.count(),
                "zones": Zone.objects.count(),
            },
            # ── chart data ─────────────────────────────────────────────
            "det_severity_chart": json.dumps({
                "labels": ["Low", "Medium", "High", "Critical"],
                "data": [
                    det_by_severity.get("low", 0),
                    det_by_severity.get("medium", 0),
                    det_by_severity.get("high", 0),
                    det_by_severity.get("critical", 0),
                ],
            }),
            "pkg_status_chart": json.dumps({
                "labels": [label for _, label in Package._meta.get_field("status").choices],
                "data": [
                    pkg_by_status.get(val, 0)
                    for val, _ in Package._meta.get_field("status").choices
                ],
            }),
            # ── top detection locations ────────────────────────────────
            "top_detection_locations": list(
                Detection.objects.filter(
                    status__in=_ACTIVE_DETECTION_STATUSES, address__isnull=False
                ).values("address__locality_name", "address__country_name")
                .annotate(count=Count("id"), avg_sev=Avg(_SEVERITY_SCORE))
                .order_by("-avg_sev", "-count")[:10]
            ),
        }, self.request))
        return context
