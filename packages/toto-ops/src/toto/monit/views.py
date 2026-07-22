import json
import math
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from toto.ui import PageProcessor

from . import collectors
from .models import Snapshot

# Chart series colors: validated per co-occurring chart set for light AND dark
# surfaces (dataviz validator); each dataset carries its dark variant and the
# template swaps them client-side when darkMode is on. Color follows the
# entity across charts: system=blue, web tier=magenta, DB=green,
# Redis=yellow (dashed), error series=violet (dashed), capacity bound=gray.
BLUE = ("#2a78d6", "#3987e5")
MAGENTA = ("#e87ba4", "#d55181")
GREEN = ("#008300", "#008300")
YELLOW = ("#eda100", "#c98500")
VIOLET = ("#4a3aa7", "#9085e9")
GRAY = ("#9ca3af", "#6b7280")

WINDOW_HOURS = 48
MAX_POINTS = 180


class MonitAccessMixin:
    """Superuser-only gate (same contract as nomad's _SuperuserView)."""

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not (user.is_authenticated and user.is_superuser):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def _downsample(snapshots, max_points=MAX_POINTS):
    if len(snapshots) <= max_points:
        return snapshots
    stride = math.ceil(len(snapshots) / max_points)
    sampled = snapshots[::stride]
    if sampled[-1] is not snapshots[-1]:
        sampled.append(snapshots[-1])
    return sampled


def _labels(snapshots):
    if not snapshots:
        return []
    multi_day = timezone.localtime(snapshots[0].created).date() != \
        timezone.localtime(snapshots[-1].created).date()
    fmt = "%d.%m %H:%M" if multi_day else "%H:%M"
    return [timezone.localtime(s.created).strftime(fmt) for s in snapshots]


def _series(snapshots, attr, scale=1.0, digits=2):
    out = []
    for snap in snapshots:
        value = getattr(snap, attr)
        out.append(round(value * scale, digits) if value is not None else None)
    return out


def _per_worker_rates(snapshots, attr):
    """Per-minute deltas between consecutive snapshots of the SAME web worker.

    Counters live in one uvicorn worker process per scrape; pairing on
    web_process_start keeps rates honest (different worker or a restart
    produces a gap, never a spike). max(0, delta) guards counter resets.
    """
    rates = [None]
    for prev, cur in zip(snapshots, snapshots[1:]):
        rate = None
        if (prev.web_process_start is not None
                and prev.web_process_start == cur.web_process_start
                and getattr(prev, attr) is not None
                and getattr(cur, attr) is not None):
            minutes = (cur.created - prev.created).total_seconds() / 60.0
            if minutes > 0:
                rate = round(max(0, getattr(cur, attr) - getattr(prev, attr)) / minutes, 2)
        rates.append(rate)
    return rates


def _dataset(label, data, colors, *, dashed=False, span_gaps=True):
    ds = {
        "label": label,
        "data": data,
        "borderColor": colors[0],
        "darkBorderColor": colors[1],
        "backgroundColor": "transparent",
        "borderWidth": 2,
        "pointRadius": 0,
        "pointHoverRadius": 4,
        "tension": 0.25,
        "spanGaps": span_gaps,
    }
    if dashed:
        ds["borderDash"] = [6, 4]
    return ds


def _chart(labels, datasets, *, y_title=""):
    options = {
        "animation": False,
        "plugins": {"legend": {"display": len(datasets) > 1}},
        "scales": {
            "x": {"ticks": {"maxTicksLimit": 8, "autoSkip": True}},
            "y": {"beginAtZero": True,
                  "title": {"display": bool(y_title), "text": y_title}},
        },
    }
    return json.dumps({"chart_type": "line", "labels": labels,
                       "datasets": datasets, "options": options})


class OverviewView(MonitAccessMixin, TemplateView):
    template_name = "monit/overview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()

        # Relevance: only measure and render what this host actually runs.
        has_nomad = apps.is_installed("toto.nomad")
        has_aster = apps.is_installed("toto.aster")
        has_prometheus = apps.is_installed("django_prometheus")
        celery_configured = bool(getattr(settings, "CELERY_BROKER_URL", ""))
        redis_configured = (
            "django_redis" in settings.CACHES.get("default", {}).get("BACKEND", "")
            or bool(getattr(settings, "MONIT_REDIS_URL", "")))
        web_scrape_enabled = bool(getattr(settings, "MONIT_WEB_METRICS_URL", ""))
        context.update({
            "has_nomad": has_nomad,
            "has_aster": has_aster,
            "has_prometheus": has_prometheus,
            "celery_configured": celery_configured,
            "redis_configured": redis_configured,
            "web_scrape_enabled": web_scrape_enabled,
        })

        # live panel — measured in THIS web process/container, this page view
        context["live_system"] = collectors.collect_system(cpu_window=0.2)
        context["live_db"] = collectors.collect_db()
        context["live_redis"] = collectors.collect_redis() if redis_configured else None
        context["live_celery"] = collectors.collect_celery() if celery_configured else None
        context["live_tor"] = collectors.collect_tor() if has_nomad else None
        context["live_aster"] = collectors.collect_aster() if has_aster else None
        context["live_requests"] = (
            collectors.collect_request_metrics() if has_prometheus else None)

        window_start = now - timedelta(hours=WINDOW_HOURS)
        snapshots = list(Snapshot.objects.filter(created__gte=window_start)
                         .order_by("created"))
        snapshots = _downsample(snapshots)
        labels = _labels(snapshots)

        context["has_history"] = bool(snapshots)
        context["snapshot_count"] = len(snapshots)
        context["window_hours"] = WINDOW_HOURS
        context["latest_snapshot"] = snapshots[-1] if snapshots else None
        starts = {s.web_process_start for s in snapshots
                  if s.web_process_start is not None}
        context["web_restarts"] = max(0, len(starts) - 1)

        if snapshots:
            context["cpu_chart_json"] = _chart(
                labels, [_dataset("CPU %", _series(snapshots, "sys_cpu_percent"), BLUE)],
                y_title="%")
            context["load_chart_json"] = _chart(
                labels, [_dataset("Load 1m (host)", _series(snapshots, "sys_load_1m"), BLUE)])
            mb = 1.0 / (1024 * 1024)
            mem_datasets = [_dataset("Sampler used (MB)",
                                     _series(snapshots, "sys_mem_used_bytes", mb, 1), BLUE)]
            if web_scrape_enabled:
                mem_datasets.append(_dataset(
                    "Web worker RSS (MB)", _series(snapshots, "web_rss_bytes", mb, 1), MAGENTA))
            context["mem_chart_json"] = _chart(labels, mem_datasets, y_title="MB")
            gb = 1.0 / (1024 ** 3)
            context["disk_chart_json"] = _chart(
                labels,
                [_dataset("Disk used (GB)", _series(snapshots, "sys_disk_used_bytes", gb, 2), BLUE),
                 _dataset("Capacity (GB)", _series(snapshots, "sys_disk_total_bytes", gb, 2), GRAY, dashed=True)],
                y_title="GB")
            latency_datasets = [_dataset("Database (ms)",
                                          _series(snapshots, "db_latency_ms"), GREEN)]
            if redis_configured:
                latency_datasets.append(_dataset(
                    "Redis (ms)", _series(snapshots, "redis_latency_ms"), YELLOW, dashed=True))
            if web_scrape_enabled:
                latency_datasets.append(_dataset(
                    "Web /metrics (ms)", _series(snapshots, "web_latency_ms"), MAGENTA))
            context["latency_chart_json"] = _chart(labels, latency_datasets, y_title="ms")
            if web_scrape_enabled:
                context["rate_chart_json"] = _chart(
                    labels,
                    [_dataset("Requests/min", _per_worker_rates(snapshots, "web_requests_total"), MAGENTA),
                     _dataset("5xx/min", _per_worker_rates(snapshots, "web_responses_5xx"), VIOLET, dashed=True)],
                    y_title="per min")

        return PageProcessor().decorate(context, self.request)


class HealthView(View):
    """Public minimal health endpoint (safe for Tor exposure: no details)."""

    def get(self, request, *args, **kwargs):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            response = JsonResponse({"status": "ok"})
        except Exception:
            response = JsonResponse({"status": "error"}, status=503)
        response["Cache-Control"] = "no-store"
        return response
