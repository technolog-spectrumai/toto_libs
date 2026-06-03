import json

from django.http import JsonResponse
from django.utils.timezone import localtime, now
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from toto.telegraph.api_views import CorsApiView
from .models import ScheduledEvent, EventInvite


def _event_to_dict(event):
    return {
        "id": str(event.id),
        "title": event.title,
        "description": event.description,
        "start_time": localtime(event.start_time).isoformat(),
        "end_time": localtime(event.end_time).isoformat(),
        "category": event.category.name if event.category else None,
        "public": event.public,
        "owner": event.owner.display_name if event.owner else None,
        "address": str(event.address) if event.address else None,
        "capacity": event.capacity,
        "requires_registration": event.requires_registration,
    }


def _invite_to_dict(invite):
    return {
        "id": str(invite.id),
        "event_id": str(invite.event_id),
        "event_title": invite.event.title,
        "event_start": localtime(invite.event.start_time).isoformat(),
        "event_end": localtime(invite.event.end_time).isoformat(),
        "event_category": invite.event.category.name if invite.event.category else None,
        "status": invite.status,
        "sent_at": invite.sent_at.isoformat(),
    }


@method_decorator(csrf_exempt, name="dispatch")
class EventListApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        current_time = now()
        qs = (
            ScheduledEvent.objects
            .select_related("category", "owner", "address")
            .order_by("start_time")
        )
        events = list(qs)
        upcoming_qs = [e for e in events if e.start_time >= current_time]
        past_qs = [e for e in events if e.start_time < current_time]

        return JsonResponse({
            "events": [_event_to_dict(e) for e in events],
            "total": len(events),
            "upcoming_count": len(upcoming_qs),
            "past_count": len(past_qs),
            "upcoming": [_event_to_dict(e) for e in upcoming_qs[:5]],
        })


@method_decorator(csrf_exempt, name="dispatch")
class EventDetailApiView(CorsApiView):
    def get(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            event = (
                ScheduledEvent.objects
                .select_related("category", "owner", "address")
                .prefetch_related("organizers")
                .get(pk=pk)
            )
        except ScheduledEvent.DoesNotExist:
            return JsonResponse({"error": "Not found."}, status=404)

        d = _event_to_dict(event)
        d["organizers"] = [p.display_name for p in event.organizers.all()]
        d["accepted_count"] = event.invites.filter(status=EventInvite.Status.ACCEPTED).count()
        d["invite_count"] = event.invites.count()
        return JsonResponse(d)


@method_decorator(csrf_exempt, name="dispatch")
class MyInvitesApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        try:
            person = request.user.community_profile
        except Exception:
            return JsonResponse({"invites": [], "pending_count": 0})

        invites = (
            EventInvite.objects
            .filter(person=person)
            .select_related("event", "event__category")
            .order_by("-sent_at")
        )

        return JsonResponse({
            "invites": [_invite_to_dict(i) for i in invites],
            "pending_count": invites.filter(status=EventInvite.Status.PENDING).count(),
        })


@method_decorator(csrf_exempt, name="dispatch")
class InviteRespondApiView(CorsApiView):
    def post(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        try:
            invite = EventInvite.objects.select_related("person", "event").get(pk=pk)
        except EventInvite.DoesNotExist:
            return JsonResponse({"error": "Not found."}, status=404)

        try:
            person = request.user.community_profile
        except Exception:
            return JsonResponse({"error": "No profile."}, status=403)

        if invite.person != person:
            return JsonResponse({"error": "Forbidden."}, status=403)

        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        action = data.get("action")
        if action not in ("accept", "decline"):
            return JsonResponse({"error": "Invalid action."}, status=400)

        invite.status = (
            EventInvite.Status.ACCEPTED if action == "accept"
            else EventInvite.Status.DECLINED
        )
        invite.responded_at = now()
        invite.save(update_fields=["status", "responded_at"])

        return JsonResponse({"ok": True, "status": invite.status})
