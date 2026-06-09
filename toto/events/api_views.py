import json
from datetime import datetime

from django.http import JsonResponse
from django.utils.timezone import localtime, now, make_aware
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from toto.api.cors import CorsApiView, MeshGatedApiView
from .models import EventCategory, ScheduledEvent, EventInvite


def _event_to_dict(event):
    return {
        "id": str(event.id),
        "title": event.title,
        "description": event.description,
        "start_time": localtime(event.start_time).isoformat(),
        "end_time": localtime(event.end_time).isoformat(),
        "category": event.category.name if event.category else None,
        "category_id": event.category_id,
        "public": event.public,
        "owner": event.owner.display_name if event.owner else None,
        "address": str(event.address) if event.address else None,
        "address_id": event.address_id,
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


def _require_auth(request):
    if not request.user or not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)
    return None


@method_decorator(csrf_exempt, name="dispatch")
class EventListApiView(MeshGatedApiView):
    def get(self, request):
        err = _require_auth(request)
        if err:
            return err

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

    def post(self, request):
        err = _require_auth(request)
        if err:
            return err

        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        title = (data.get("title") or "").strip()
        if not title:
            return JsonResponse({"error": "Title is required."}, status=400)

        start_raw = data.get("start_time", "")
        end_raw = data.get("end_time", "")
        try:
            start_dt = make_aware(datetime.fromisoformat(start_raw))
            end_dt = make_aware(datetime.fromisoformat(end_raw))
        except Exception:
            return JsonResponse({"error": "Invalid start_time or end_time (use ISO 8601)."}, status=400)

        if end_dt <= start_dt:
            return JsonResponse({"error": "end_time must be after start_time."}, status=400)

        category = None
        if data.get("category_id"):
            try:
                category = EventCategory.objects.get(pk=data["category_id"])
            except EventCategory.DoesNotExist:
                return JsonResponse({"error": "Category not found."}, status=400)

        address = None
        if data.get("address_id"):
            from toto.locations.models import Address
            try:
                address = Address.objects.get(pk=data["address_id"])
            except Address.DoesNotExist:
                return JsonResponse({"error": "Address not found."}, status=400)

        try:
            owner = request.user.community_profile
        except Exception:
            owner = None

        event = ScheduledEvent.objects.create(
            title=title,
            description=data.get("description", ""),
            start_time=start_dt,
            end_time=end_dt,
            category=category,
            address=address,
            capacity=data.get("capacity") or None,
            requires_registration=bool(data.get("requires_registration", False)),
            public=bool(data.get("public", True)),
            owner=owner,
        )
        if owner:
            event.organizers.add(owner)

        d = _event_to_dict(event)
        d["organizers"] = [owner.display_name] if owner else []
        d["accepted_count"] = 0
        d["invite_count"] = 0
        return JsonResponse(d, status=201)


@method_decorator(csrf_exempt, name="dispatch")
class EventDetailApiView(MeshGatedApiView):
    def get(self, request, pk):
        err = _require_auth(request)
        if err:
            return err
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
        d["organizer_ids"] = list(event.organizers.values_list("pk", flat=True))
        d["accepted_count"] = event.invites.filter(status=EventInvite.Status.ACCEPTED).count()
        d["invite_count"] = event.invites.count()
        return JsonResponse(d)


@method_decorator(csrf_exempt, name="dispatch")
class EventInviteApiView(CorsApiView):
    """Send an invite to a person for an event."""

    def post(self, request, pk):
        err = _require_auth(request)
        if err:
            return err

        try:
            event = ScheduledEvent.objects.prefetch_related("organizers").get(pk=pk)
        except ScheduledEvent.DoesNotExist:
            return JsonResponse({"error": "Event not found."}, status=404)

        try:
            requester = request.user.community_profile
        except Exception:
            requester = None

        is_organizer = (
            request.user.is_superuser or request.user.is_staff or
            (requester and (
                event.owner == requester or
                event.organizers.filter(pk=requester.pk).exists()
            ))
        )
        if not is_organizer:
            return JsonResponse({"error": "Only organizers can invite people."}, status=403)

        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        person_id = data.get("person_id")
        if not person_id:
            return JsonResponse({"error": "person_id is required."}, status=400)

        from toto.people.models import Person
        try:
            person = Person.objects.get(pk=person_id)
        except Person.DoesNotExist:
            return JsonResponse({"error": "Person not found."}, status=404)

        invite, created = EventInvite.objects.get_or_create(event=event, person=person)
        return JsonResponse({
            "ok": True,
            "created": created,
            "invite_id": str(invite.id),
            "person": person.display_name,
            "status": invite.status,
        }, status=201 if created else 200)


@method_decorator(csrf_exempt, name="dispatch")
class FormDataApiView(MeshGatedApiView):
    """Return categories, addresses, and people for the create-event form."""

    def get(self, request):
        err = _require_auth(request)
        if err:
            return err

        from toto.locations.models import Address
        from toto.people.models import Person

        categories = list(
            EventCategory.objects.values("id", "name").order_by("name")
        )
        addresses = [
            {"id": a.id, "display": str(a)}
            for a in Address.objects.order_by("locality_name", "street")[:200]
        ]
        people = [
            {"id": p.id, "name": p.display_name}
            for p in Person.objects.order_by("display_name")[:500]
        ]

        return JsonResponse({"categories": categories, "addresses": addresses, "people": people})


@method_decorator(csrf_exempt, name="dispatch")
class MyInvitesApiView(MeshGatedApiView):
    def get(self, request):
        err = _require_auth(request)
        if err:
            return err

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
        err = _require_auth(request)
        if err:
            return err

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
