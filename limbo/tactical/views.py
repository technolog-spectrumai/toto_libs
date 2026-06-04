from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views.generic import TemplateView

from toto.core.page import PageProcessor


def _page(context, request):
    return PageProcessor().decorate(context, request)


class CommandView(LoginRequiredMixin, TemplateView):
    template_name = "tactical/command.html"

    def get_context_data(self, **kwargs):
        from toto.tactical.plugins import FieldMetricsPlugin
        context = super().get_context_data(**kwargs)
        sections = FieldMetricsPlugin.get_sections(request=self.request)
        return _page({
            **context,
            "sections": sections,
            "map_data_url": "/tactical/api/map/",
        }, self.request)


class MapDataView(LoginRequiredMixin, TemplateView):
    def get(self, request, *args, **kwargs):
        from toto.tactical.plugins import FieldMapPlugin
        features = FieldMapPlugin.get_features(request=request)
        return JsonResponse({"type": "FeatureCollection", "features": features})


class MetricsView(LoginRequiredMixin, TemplateView):
    template_name = "tactical/metrics.html"

    def get_context_data(self, **kwargs):
        from toto.tactical.plugins import FieldMetricsPlugin
        context = super().get_context_data(**kwargs)
        sections = FieldMetricsPlugin.get_sections(request=self.request)
        return _page({
            **context,
            "sections": sections,
        }, self.request)


class MetricsDataView(LoginRequiredMixin, TemplateView):
    """JSON API — read-only metrics for all registered sections (mobile-friendly)."""

    def get(self, request, *args, **kwargs):
        from toto.tactical.plugins import FieldMetricsPlugin
        sections = FieldMetricsPlugin.get_sections(request=request)
        ribbon = FieldMetricsPlugin.get_ribbon(request=request)
        return JsonResponse({
            "ribbon": ribbon,
            "sections": sections,
        })
