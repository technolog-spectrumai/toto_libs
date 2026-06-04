import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView
from django.views.decorators.http import require_POST

from toto.core.page import PageProcessor

from .forms import DetectionHelpForm, DetectionMapCreateForm
from .models import Detection, DetectionCategory
from .services import (
    create_detection_help_task,
    detection_map_feature,
    ensure_detection_mitigation_task,
    person_for_user,
)


def _detections_parent_template():
    return "detections/base_standalone.html"


class DetectionsContextMixin:
    def get_context_data(self, **kwargs):
        from toto.vault.models import Bucket, VaultDirectory
        context = super().get_context_data(**kwargs)
        context["detections_parent_template"] = _detections_parent_template()
        buckets = list(Bucket.objects.all().order_by("name"))
        directories = list(
            VaultDirectory.objects.select_related("bucket").order_by("bucket__name", "name")
        )
        context["export_buckets_json"] = json.dumps([{"id": b.id, "name": b.name} for b in buckets])
        context["export_directories_json"] = json.dumps([
            {"id": d.id, "bucket_id": d.bucket_id, "path": d.full_path()}
            for d in directories
        ])
        return PageProcessor().decorate(context, self.request)


class DetectionListView(DetectionsContextMixin, ListView):
    model = Detection
    template_name = "detections/detection_list.html"
    context_object_name = "detections"
    paginate_by = 30

    def get_queryset(self):
        qs = Detection.objects.select_related(
            "category",
            "address",
            "zone",
            "route",
            "reported_by",
            "mitigation_task",
            "mitigation_task__column",
        )
        q = self.request.GET.get("q")
        status = self.request.GET.get("status")
        severity = self.request.GET.get("severity")
        det_type = self.request.GET.get("type")
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
        if status:
            qs = qs.filter(status=status)
        if severity:
            qs = qs.filter(severity=severity)
        if det_type:
            qs = qs.filter(detection_type=det_type)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["categories"] = DetectionCategory.objects.filter(is_active=True, parent__isnull=True)
        context["severity_choices"] = Detection.SEVERITY_CHOICES
        context["type_choices"] = Detection.TYPE_CHOICES
        context["status_choices"] = Detection.STATUS_CHOICES
        context["detection_features"] = [
            feature for feature in (
                detection_map_feature(detection)
                for detection in context["detections"]
            )
            if feature
        ]
        return context


class DetectionDetailView(DetectionsContextMixin, DetailView):
    model = Detection
    template_name = "detections/detection_detail.html"
    context_object_name = "detection"

    def get_queryset(self):
        return Detection.objects.select_related(
            "category",
            "address",
            "zone",
            "route",
            "reported_by",
            "mitigation_task",
            "mitigation_task__column",
            "mitigation_task__mission",
            "mitigation_task__assignee",
            "mitigation_task__reviewer",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["handles"] = self.object.handles.select_related("assigned_to")
        try:
            context["promoted_incident_pk"] = str(self.object.promoted_incident.pk)
        except Exception:
            context["promoted_incident_pk"] = ""
        return context


class DetectionCreateView(LoginRequiredMixin, DetectionsContextMixin, View):
    template_name = "detections/detection_form.html"

    def get(self, request):
        form = DetectionMapCreateForm(reporter=person_for_user(request.user))
        return self._render(request, form)

    def post(self, request):
        form = DetectionMapCreateForm(request.POST, reporter=person_for_user(request.user))
        if form.is_valid():
            detection = form.save()
            ensure_detection_mitigation_task(detection, owner=person_for_user(request.user))
            messages.success(request, "Detection added from the map.")
            return redirect("detections:detection-detail", pk=detection.pk)
        messages.error(request, "Check the detection details and map location.")
        return self._render(request, form)

    def _render(self, request, form):
        detections = Detection.objects.select_related("category", "address", "zone", "route")[:100]
        context = {
            "form": form,
            "detections_parent_template": _detections_parent_template(),
            "detection_features": [
                feature for feature in (
                    detection_map_feature(detection)
                    for detection in detections
                )
                if feature
            ],
        }
        return render(request, self.template_name, PageProcessor().decorate(context, request))


class DetectionHelpView(LoginRequiredMixin, DetectionsContextMixin, View):
    template_name = "detections/detection_help.html"

    def get_detection(self):
        return get_object_or_404(
            Detection.objects.select_related(
                "category",
                "address",
                "mitigation_task",
                "mitigation_task__mission",
            ),
            pk=self.kwargs["pk"],
        )

    def get(self, request, pk):
        detection = self.get_detection()
        return self._render(request, detection, DetectionHelpForm(detection=detection))

    def post(self, request, pk):
        detection = self.get_detection()
        form = DetectionHelpForm(request.POST, detection=detection)
        if not form.is_valid():
            messages.error(request, "Choose a mission for this help task.")
            return self._render(request, detection, form)

        task = create_detection_help_task(
            detection,
            mission=form.cleaned_data["mission"],
            owner=person_for_user(request.user),
            title=form.cleaned_data["title"],
            description=form.cleaned_data["description"],
            required_skills=form.cleaned_data.get("required_skills"),
        )
        messages.success(request, f"Help task created in {task.mission.title}.")
        return redirect("detections:detection-detail", pk=detection.pk)

    def _render(self, request, detection, form):
        context = PageProcessor().decorate({
            "detection": detection,
            "form": form,
            "detections_parent_template": _detections_parent_template(),
        }, request)
        return render(request, self.template_name, context)


class DetectionDashboardView(LoginRequiredMixin, DetectionsContextMixin, TemplateView):
    template_name = "detections/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        recent = Detection.objects.filter(
            status__in=["new", "acknowledged", "handling"],
        ).select_related(
            "category",
            "reported_by",
            "mitigation_task",
            "mitigation_task__column",
        )[:20]
        status_counts = list(
            Detection.objects.values("status").annotate(count=Count("id")).order_by("status")
        )
        severity_counts = list(
            Detection.objects.values("severity").annotate(count=Count("id")).order_by("severity")
        )
        context.update({
            "recent_detections": recent,
            "active_count": Detection.objects.filter(status__in=["new", "acknowledged", "handling"]).count(),
            "task_count": Detection.objects.filter(mitigation_task__isnull=False).count(),
            "unassigned_count": Detection.objects.filter(mitigation_task__isnull=True).count(),
            "status_chart_data": {
                "labels": [dict(Detection.STATUS_CHOICES).get(row["status"], row["status"]) for row in status_counts],
                "data": [row["count"] for row in status_counts],
            },
            "severity_chart_data": {
                "labels": [dict(Detection.SEVERITY_CHOICES).get(row["severity"], row["severity"]) for row in severity_counts],
                "data": [row["count"] for row in severity_counts],
            },
        })
        return context


@require_POST
def api_export_layers(request):
    """Trigger a detections layer export workflow run."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    bucket_id = data.get("bucket_id")
    if not bucket_id:
        return JsonResponse({"error": "Select a target bucket."}, status=400)

    layer_slugs = data.get("layer_slugs") or []
    if not layer_slugs:
        return JsonResponse({"error": "Select at least one layer."}, status=400)

    from toto.celery_utils import celery_available
    if not celery_available():
        return JsonResponse({"error": "No Celery worker running."}, status=503)

    from toto.workflows.api import trigger_workflow
    from toto.workflows.models import Workflow
    from toto.detections.predefined_tasks import DETECTIONS_EXPORT_SLUG

    try:
        run = trigger_workflow(DETECTIONS_EXPORT_SLUG, {
            "data": {
                "layer_slugs": layer_slugs,
                "bucket_id": int(bucket_id),
                "directory_id": int(data["directory_id"]) if data.get("directory_id") else None,
                "title": data.get("title", "").strip() or None,
                "password": data.get("password") or None,
                "owner_id": request.user.pk,
            }
        })
        return JsonResponse({"run_id": run.id})
    except Workflow.DoesNotExist:
        return JsonResponse(
            {"error": "Export workflow not seeded. Run: manage.py seed_detections_workflows"},
            status=503,
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


def api_export_run_status(request, run_id):
    """Check a workflow run status and return download_url when available."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    from toto.workflows.models import WorkflowNodeRun, WorkflowRun

    try:
        run = WorkflowRun.objects.get(pk=run_id)
    except WorkflowRun.DoesNotExist:
        return JsonResponse({"error": "Run not found."}, status=404)

    error = ""
    if run.status == WorkflowRun.FAILED:
        failed_node = run.node_runs.exclude(error="").select_related("node").first()
        if failed_node:
            error = failed_node.error

    download_url = None
    for nr in run.node_runs.filter(status=WorkflowNodeRun.COMPLETED).select_related("node"):
        file_data = ((nr.output_data or {}).get("data") or {})
        if file_data.get("download_url"):
            download_url = file_data["download_url"]
            break

    return JsonResponse({"status": run.status, "error": error, "download_url": download_url})
