import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.serializers.json import DjangoJSONEncoder
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.timezone import localtime, now
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, ListView

from .forms import ScheduledEventForm
from .models import ScheduledEvent
from toto.ui import PageProcessor


def events_render(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


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


class EventDetailView(LoginRequiredMixin, DetailView):
    model = ScheduledEvent
    template_name = 'events/event_detail.html'
    context_object_name = 'event'
    login_url = reverse_lazy('core:login')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["category"] = self.object.category
        context["organizer"] = self.object.organizer
        return PageProcessor().decorate(context, self.request)


def event_create(request):
    if request.method == "POST":
        form = ScheduledEventForm(request.POST)
        if form.is_valid():
            event = form.save()
            return redirect("events:event_detail", pk=event.pk)
    else:
        form = ScheduledEventForm()

    return events_render(request, "events/event_form.html", {
        "form": form,
        "title": _("New event"),
    })
