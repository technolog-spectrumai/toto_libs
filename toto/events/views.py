import json

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.timezone import localtime, now
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, ListView

from .forms import ScheduledEventForm
from .models import Availability, EventInvite, ScheduledEvent
from toto.people.models import Person
from toto.ui import PageProcessor


def events_render(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


def _current_person(user):
    """Return the Person linked to this user, or None."""
    try:
        return user.community_profile
    except Exception:
        return None


def _is_organizer(user, event):
    """True if the user is a superuser, the event owner, or one of the organizers."""
    if user.is_superuser or user.is_staff:
        return True
    person = _current_person(user)
    if not person:
        return False
    return event.owner == person or event.organizers.filter(pk=person.pk).exists()


# ─── Calendar ────────────────────────────────────────────────────────────────

class EventCalendarView(ListView):
    model = ScheduledEvent
    template_name = 'events/calendar.html'
    context_object_name = 'events'
    ordering = ['start_time']

    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return ScheduledEvent.objects.filter(public=True).order_by('start_time')
        return ScheduledEvent.objects.all().order_by('start_time')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        current_time = now()

        calendar_events = [
            {
                "title": event.title,
                "start": localtime(event.start_time).isoformat(),
                "end": localtime(event.end_time).isoformat(),
                "url": reverse("events:event_detail", args=[event.pk])
            }
            for event in context["events"]
        ]
        context["calendar_events"] = json.dumps(calendar_events, cls=DjangoJSONEncoder)

        qs = context["events"]
        context["total_events"] = qs.count()
        context["upcoming_events"] = qs.filter(start_time__gte=current_time)[:5]
        context["upcoming_count"] = qs.filter(start_time__gte=current_time).count()
        context["past_count"] = qs.filter(start_time__lt=current_time).count()

        decorated_context = PageProcessor().decorate(context, self.request)
        theme_colors = decorated_context.get("theme", {}).get("colors", {})
        decorated_context["chart_colors"] = {
            "background_light": theme_colors.get("accent-light", "#36A2EB"),
            "text_light": theme_colors.get("text-main-light", "#000000"),
        }
        return decorated_context


# ─── Event detail ─────────────────────────────────────────────────────────────

class EventDetailView(LoginRequiredMixin, DetailView):
    model = ScheduledEvent
    template_name = 'events/event_detail.html'
    context_object_name = 'event'
    login_url = reverse_lazy('core:login')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["organizers"] = self.object.organizers.all()

        person = _current_person(self.request.user)
        context["my_invite"] = (
            EventInvite.objects.filter(event=self.object, person=person).first()
            if person else None
        )
        context["user_is_organizer"] = _is_organizer(self.request.user, self.object)

        return PageProcessor().decorate(context, self.request)


# ─── Event create ─────────────────────────────────────────────────────────────

@login_required
def event_create(request):
    if request.method == "POST":
        form = ScheduledEventForm(request.POST)
        if form.is_valid():
            event = form.save(commit=False)
            person = _current_person(request.user)
            event.owner = person
            event.save()
            if person:
                event.organizers.add(person)
            return redirect("events:event_detail", pk=event.pk)
    else:
        form = ScheduledEventForm()

    return events_render(request, "events/event_form.html", {
        "form": form,
        "title": _("New event"),
    })


# ─── Planning ────────────────────────────────────────────────────────────────

def _avail_status(person, event):
    """Return (status_key, first_conflict_or_None) for a person against an event window."""
    overlapping = person.availabilities.filter(
        start_time__lt=event.end_time,
        end_time__gt=event.start_time,
    )
    blocker = overlapping.filter(blocks_scheduling=True).first()
    if blocker:
        return "blocked", blocker

    soft = overlapping.first()
    if soft:
        return "busy", soft

    explicit = person.availabilities.filter(
        start_time__lte=event.start_time,
        end_time__gte=event.end_time,
        availability_type=Availability.AvailabilityType.AVAILABLE,
    ).first()
    if explicit:
        return "available", None

    return "unknown", None


@login_required
def event_availability_api(request):
    person_id = request.GET.get("person_id")
    event_id = request.GET.get("event_id")
    person = get_object_or_404(Person, pk=person_id)
    event = get_object_or_404(ScheduledEvent, pk=event_id)

    status_key, conflict = _avail_status(person, event)

    overlapping = list(
        person.availabilities
        .filter(start_time__lt=event.end_time, end_time__gt=event.start_time)
        .order_by("start_time")
    )

    return JsonResponse({
        "person": {
            "name": person.display_name,
            "initials": (person.display_name or "?")[0].upper(),
        },
        "status": status_key,
        "conflict": {
            "type": conflict.get_availability_type_display(),
            "reason": conflict.reason,
            "blocks": conflict.blocks_scheduling,
        } if conflict else None,
        "event": {
            "date": localtime(event.start_time).strftime("%b %-d, %Y"),
            "start": localtime(event.start_time).strftime("%H:%M"),
            "end": localtime(event.end_time).strftime("%H:%M"),
        },
        "availabilities": [
            {
                "type": a.get_availability_type_display(),
                "start": localtime(a.start_time).strftime("%H:%M"),
                "end": localtime(a.end_time).strftime("%H:%M"),
                "reason": a.reason,
                "blocks": a.blocks_scheduling,
            }
            for a in overlapping
        ],
    })


@login_required
def event_plan(request, pk):
    event = get_object_or_404(
        ScheduledEvent.objects.select_related("owner", "address"),
        pk=pk,
    )

    if request.method == "POST":
        if not _is_organizer(request.user, event):
            return HttpResponseForbidden()
        person_ids = request.POST.getlist("person_ids")
        for pid in person_ids:
            try:
                person = Person.objects.get(pk=pid)
                EventInvite.objects.get_or_create(event=event, person=person)
            except Person.DoesNotExist:
                pass
        return redirect("events:event_plan", pk=pk)

    invites = (
        event.invites
        .select_related("person")
        .order_by("person__display_name")
    )

    invite_rows = []
    for invite in invites:
        status_key, conflict = _avail_status(invite.person, event)
        invite_rows.append({
            "invite": invite,
            "person": invite.person,
            "avail_status": status_key,
            "conflict": conflict,
        })

    invited_ids = invites.values_list("person_id", flat=True)
    uninvited = Person.objects.exclude(pk__in=invited_ids).order_by("display_name")

    accepted = invites.filter(status=EventInvite.Status.ACCEPTED).count()
    declined = invites.filter(status=EventInvite.Status.DECLINED).count()
    pending = invites.filter(status=EventInvite.Status.PENDING).count()

    return events_render(request, "events/event_plan.html", {
        "event": event,
        "invite_rows": invite_rows,
        "uninvited": uninvited,
        "accepted_count": accepted,
        "declined_count": declined,
        "pending_count": pending,
        "user_is_organizer": _is_organizer(request.user, event),
    })


# ─── Invite response ─────────────────────────────────────────────────────────

@login_required
def invite_respond(request, pk):
    invite = get_object_or_404(EventInvite.objects.select_related("person", "event"), pk=pk)

    person = _current_person(request.user)
    if not person or invite.person != person:
        return HttpResponseForbidden()

    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("accept", "decline"):
            invite.status = (
                EventInvite.Status.ACCEPTED if action == "accept"
                else EventInvite.Status.DECLINED
            )
            invite.responded_at = now()
            invite.save(update_fields=["status", "responded_at"])

    return redirect("events:my_invites")


# ─── My invites ──────────────────────────────────────────────────────────────

@login_required
def my_invites(request):
    person = _current_person(request.user)

    invites = (
        EventInvite.objects
        .filter(person=person)
        .select_related("event", "event__owner", "event__category")
        .order_by("-sent_at")
        if person else EventInvite.objects.none()
    )

    pending = invites.filter(status=EventInvite.Status.PENDING)
    accepted = invites.filter(status=EventInvite.Status.ACCEPTED)
    declined = invites.filter(status=EventInvite.Status.DECLINED)

    return events_render(request, "events/my_invites.html", {
        "pending_invites": pending,
        "accepted_invites": accepted,
        "declined_invites": declined,
        "person": person,
        "no_profile": person is None,
    })
