import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from toto.api.cors import CorsApiView
from .models import Incident

_VALID_SEVERITIES = {"low", "medium", "high", "critical"}
_VALID_STATUSES = {"open", "contained", "resolved", "closed"}
_VALID_INCIDENT_TYPES_SET = {"emergency", "hazard", "breach", "infrastructure", "environmental", "other"}


@method_decorator(csrf_exempt, name="dispatch")
class IncidentsMapApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        qs = (
            Incident.objects
            .select_related("address", "zone", "category")
            .order_by("-created_at")[:200]
        )

        incidents = []
        features = []

        for inc in qs:
            geom = None
            raw = inc.map_geometry
            if raw:
                try:
                    if raw.geom_type == "Point":
                        geom = json.loads(raw.geojson)
                    else:
                        geom = json.loads(raw.centroid.geojson)
                except Exception:
                    pass

            item = {
                "id": inc.pk,
                "title": inc.title,
                "severity": inc.severity,
                "status": inc.status,
                "incident_type": inc.incident_type,
                "location": inc.location_label or "",
                "has_geometry": bool(geom),
                "created_at": inc.created_at.isoformat(),
            }
            incidents.append(item)
            if geom:
                features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "kind": "incident",
                        "label": inc.title,
                        "severity": inc.severity,
                        "incident_type": inc.incident_type,
                        "status": inc.status,
                    },
                })

        return JsonResponse({
            "incidents": incidents,
            "geojson": {"type": "FeatureCollection", "features": features},
        })


@method_decorator(csrf_exempt, name="dispatch")
class IncidentDetailApiView(CorsApiView):
    def _serialize(self, inc):
        return {
            "id": str(inc.pk),
            "title": inc.title,
            "description": inc.description,
            "severity": inc.severity,
            "status": inc.status,
            "incident_type": inc.incident_type,
            "location": inc.location_label or "",
            "created_at": inc.created_at.isoformat(),
            "updated_at": inc.updated_at.isoformat(),
            "start_time": inc.start_time.isoformat() if inc.start_time else None,
            "end_time": inc.end_time.isoformat() if inc.end_time else None,
            "source_detection_id": str(inc.source_detection_id) if inc.source_detection_id else None,
            "reported_by": str(inc.reported_by) if inc.reported_by else None,
        }

    def get(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            inc = Incident.objects.select_related("address", "zone", "reported_by").get(pk=pk)
        except Incident.DoesNotExist:
            return JsonResponse({"error": "Not found."}, status=404)
        return JsonResponse(self._serialize(inc))

    def patch(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            inc = Incident.objects.select_related("address", "zone").get(pk=pk)
        except Incident.DoesNotExist:
            return JsonResponse({"error": "Not found."}, status=404)
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON."}, status=400)
        if "title" in data:
            title = str(data["title"]).strip()
            if not title:
                return JsonResponse({"error": "Title cannot be empty."}, status=400)
            inc.title = title
        if "description" in data:
            inc.description = str(data["description"])
        if "severity" in data:
            if data["severity"] not in _VALID_SEVERITIES:
                return JsonResponse({"error": "Invalid severity."}, status=400)
            inc.severity = data["severity"]
        if "status" in data:
            if data["status"] not in _VALID_STATUSES:
                return JsonResponse({"error": "Invalid status."}, status=400)
            inc.status = data["status"]
        if "incident_type" in data:
            if data["incident_type"] not in _VALID_INCIDENT_TYPES_SET:
                return JsonResponse({"error": "Invalid incident type."}, status=400)
            inc.incident_type = data["incident_type"]
        inc.save()
        return JsonResponse(self._serialize(inc))


_DETECTION_TYPE_TO_INCIDENT_TYPE = {
    "incident": "emergency",
    "hazard": "hazard",
    "anomaly": "other",
    "observation": "other",
    "other": "other",
}
_VALID_INCIDENT_TYPES = {"emergency", "hazard", "breach", "infrastructure", "environmental", "other"}


@method_decorator(csrf_exempt, name="dispatch")
class PromoteDetectionApiView(CorsApiView):
    def post(self, request, detection_pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        from toto.detections.models import Detection
        from django.utils import timezone

        try:
            detection = Detection.objects.select_related(
                "address", "zone", "reported_by"
            ).get(pk=detection_pk)
        except Detection.DoesNotExist:
            return JsonResponse({"error": "Detection not found."}, status=404)

        try:
            pi = detection.promoted_incident
            return JsonResponse({"incident_pk": str(pi.pk), "already_existed": True})
        except Exception:
            pass

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

        return JsonResponse({"incident_pk": str(inc.pk), "already_existed": False})
