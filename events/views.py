from django.views.generic import ListView
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.timezone import localtime
import json
from .models import Event
from .page import PageProcessor
from django.views.generic import DetailView
from django.urls import reverse


class EventCalendarView(ListView):
    model = Event
    template_name = 'events/calendar.html'
    context_object_name = 'events'
    ordering = ['start_time']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # 📅 Prepare calendar events
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

        # 🎨 Theme-aware chart colors
        decorated_context = PageProcessor().decorate(context, self.request)
        theme_colors = decorated_context.get("theme", {}).get("colors", {})
        decorated_context["chart_colors"] = {
            "background_light": theme_colors.get("accent-light", "#36A2EB"),
            "text_light": theme_colors.get("text-main-light", "#000000"),
        }

        return decorated_context



class EventDetailView(DetailView):
    model = Event
    template_name = 'events/event_detail.html'
    context_object_name = 'event'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Add related info if needed
        context["venture"] = self.object.venture
        context["category"] = self.object.category
        context["organizer"] = self.object.organizer

        decorated_context = PageProcessor().decorate(context, self.request)
        return decorated_context

