import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from toto.api.cors import CorsApiView
from .models import MobilizationEvent


def _event_features(event):
    features = []

    # Campaign zone
    campaign = event.kanban_campaign
    if campaign and campaign.zone and campaign.zone.geometry:
        try:
            geom = json.loads(campaign.zone.geometry.geojson)
            features.append({
                "type": "Feature",
                "properties": {
                    "kind": "campaign_zone",
                    "label": campaign.name,
                    "event_id": event.pk,
                    "event_title": event.title,
                },
                "geometry": geom,
            })
        except Exception:
            pass

    # Deployments
    for dep in event.deployments.select_related("kanban_mission__location").prefetch_related("interventions"):
        if dep.kanban_mission and dep.kanban_mission.location and dep.kanban_mission.location.geometry:
            try:
                geom = json.loads(dep.kanban_mission.location.geometry.geojson)
                done = sum(1 for iv in dep.interventions.all() if iv.status == "done")
                total_iv = dep.interventions.count()
                features.append({
                    "type": "Feature",
                    "properties": {
                        "kind": "deployment",
                        "label": dep.title,
                        "status": dep.status,
                        "priority": dep.priority,
                        "type": dep.get_deployment_type_display(),
                        "interventions_done": done,
                        "interventions_total": total_iv,
                        "event_id": event.pk,
                        "event_title": event.title,
                    },
                    "geometry": geom,
                })
            except Exception:
                pass

    # Evac routes
    for er in event.evac_routes.select_related("route"):
        if er.route_id and er.route.geometry:
            try:
                geom = json.loads(er.route.geometry.geojson)
                features.append({
                    "type": "Feature",
                    "properties": {
                        "kind": "evac_route",
                        "label": er.name,
                        "route_type": er.route_type,
                        "status": er.status,
                        "event_id": event.pk,
                        "event_title": event.title,
                    },
                    "geometry": geom,
                })
            except Exception:
                pass

    # Emergency zones
    for es in event.emergency_statuses.select_related("zone", "community__territory"):
        geom = None
        label = ""
        try:
            if es.zone and es.zone.geometry:
                geom = json.loads(es.zone.geometry.geojson)
                label = str(es.zone)
            elif (es.community and hasattr(es.community, "territory")
                  and es.community.territory and es.community.territory.geometry):
                geom = json.loads(es.community.territory.geometry.geojson)
                label = str(es.community)
        except Exception:
            geom = None
        if geom:
            features.append({
                "type": "Feature",
                "properties": {
                    "kind": "emergency_zone",
                    "label": label,
                    "level": es.level,
                    "status": es.status,
                    "event_id": event.pk,
                    "event_title": event.title,
                },
                "geometry": geom,
            })

    return features


@method_decorator(csrf_exempt, name="dispatch")
class MobilizationMapApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        qs = (
            MobilizationEvent.objects
            .filter(status__in=["standby", "active"])
            .select_related("community", "incident_type", "coordinator", "kanban_campaign__zone")
            .prefetch_related(
                "deployments__kanban_mission__location",
                "deployments__interventions",
                "evac_routes__route",
                "emergency_statuses__zone",
                "emergency_statuses__community__territory",
            )
            .order_by("-created_at")[:50]
        )

        events = []
        all_features = []

        for event in qs:
            deps = list(event.deployments.all())
            events.append({
                "id": event.pk,
                "title": event.title,
                "status": event.status,
                "community": str(event.community) if event.community else "",
                "incident_type": str(event.incident_type) if event.incident_type else "",
                "coordinator": str(event.coordinator) if event.coordinator else "",
                "deployment_count": len(deps),
                "active_deployment_count": sum(1 for d in deps if d.status == "active"),
                "started_at": event.started_at.isoformat() if event.started_at else None,
                "created_at": event.created_at.isoformat(),
                "deployments": [
                    {
                        "id": d.pk,
                        "title": d.title,
                        "status": d.status,
                        "deployment_type": d.get_deployment_type_display(),
                        "priority": d.priority,
                        "objective": d.objective,
                    }
                    for d in deps
                ],
            })
            all_features.extend(_event_features(event))

        return JsonResponse({
            "events": events,
            "geojson": {"type": "FeatureCollection", "features": all_features},
        })
