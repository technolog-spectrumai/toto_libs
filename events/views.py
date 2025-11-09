from django.views.generic import ListView, DetailView
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.timezone import localtime
from django.urls import reverse
from django.contrib.auth.mixins import LoginRequiredMixin
import json
from .models import Event
from .page import PageProcessor
from django.urls import reverse_lazy


class EventCalendarView(ListView):
    model = Event
    template_name = 'events/calendar.html'
    context_object_name = 'events'
    ordering = ['start_time']

    def get_queryset(self):
        # Show only public events if user is not authenticated
        if not self.request.user.is_authenticated:
            return Event.objects.filter(public=True).order_by('start_time')
        return Event.objects.all().order_by('start_time')

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


class EventDetailView(LoginRequiredMixin, DetailView):
    model = Event
    template_name = 'events/event_detail.html'
    context_object_name = 'event'
    login_url = reverse_lazy('gate:login')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Add related info
        context["venture"] = self.object.venture
        context["category"] = self.object.category
        context["organizer"] = self.object.organizer

        decorated_context = PageProcessor().decorate(context, self.request)
        return decorated_context
