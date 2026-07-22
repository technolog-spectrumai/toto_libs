import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from toto.api.cors import CorsApiView
from .models import Detection


@method_decorator(csrf_exempt, name="dispatch")
class DetectionsListApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        qs = (
            Detection.objects
            .select_related("address", "zone", "route", "category", "reported_by")
            .order_by("-start_time")[:200]
        )

        detections = []
        features = []

        for det in qs:
            geom = None
            raw = det.map_geometry
            if raw:
                try:
                    if raw.geom_type == "Point":
                        geom = json.loads(raw.geojson)
                    else:
                        geom = json.loads(raw.centroid.geojson)
                except Exception:
                    pass

            # Check if already promoted to an incident
            already_promoted = False
            promoted_incident_id = None
            try:
                pi = det.promoted_incident
                already_promoted = True
                promoted_incident_id = str(pi.pk)
            except Exception:
                pass

            item = {
                "id": str(det.pk),
                "title": det.title,
                "description": det.description,
                "severity": det.severity,
                "detection_type": det.detection_type,
                "detection_type_display": det.get_detection_type_display(),
                "status": det.status,
                "status_display": det.get_status_display(),
                "location": det.location_label or "",
                "has_geometry": bool(geom),
                "reported_by": str(det.reported_by) if det.reported_by else None,
                "already_promoted": already_promoted,
                "promoted_incident_id": promoted_incident_id,
                "start_time": det.start_time.isoformat() if det.start_time else None,
                "created_at": det.created_at.isoformat(),
            }
            detections.append(item)

            if geom:
                features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "kind": "detection",
                        "label": det.title,
                        "severity": det.severity,
                        "detection_type": det.detection_type,
                        "status": det.status,
                        "already_promoted": already_promoted,
                        "id": str(det.pk),
                    },
                })

        return JsonResponse({
            "detections": detections,
            "geojson": {"type": "FeatureCollection", "features": features},
        })


@method_decorator(csrf_exempt, name="dispatch")
class MissionsListApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        from toto.kanban.models import Mission
        missions = (
            Mission.objects
            .select_related("campaign", "campaign__project")
            .order_by("campaign__project__name", "campaign__name", "title")[:100]
        )
        return JsonResponse({
            "missions": [
                {
                    "id": m.pk,
                    "title": m.title,
                    "campaign": m.campaign.name if m.campaign else "",
                    "project": m.campaign.project.name if m.campaign and m.campaign.project else "",
                }
                for m in missions
            ]
        })


@method_decorator(csrf_exempt, name="dispatch")
class DetectionHelpApiView(CorsApiView):
    def post(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            detection = Detection.objects.get(pk=pk)
        except Detection.DoesNotExist:
            return JsonResponse({"error": "Not found."}, status=404)
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        from toto.kanban.models import Mission
        mission_id = data.get("mission_id")
        if not mission_id:
            return JsonResponse({"error": "mission_id is required."}, status=400)
        try:
            mission = Mission.objects.select_related("campaign__project").get(pk=mission_id)
        except Mission.DoesNotExist:
            return JsonResponse({"error": "Mission not found."}, status=404)

        from .services import create_detection_help_task, person_for_user
        title = str(data.get("title") or f"Help with: {detection.title}").strip()
        description = str(data.get("description") or detection.description)
        task = create_detection_help_task(
            detection,
            mission=mission,
            owner=person_for_user(request.user),
            title=title,
            description=description,
        )
        return JsonResponse({"ok": True, "task_id": task.pk, "task_title": task.title})
