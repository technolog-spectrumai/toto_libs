from __future__ import annotations

import os
import tempfile

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .forms import SpeechModelDownloadForm, TranscriptCollectionForm, TranscriptionJobForm, TranscriptSourceForm, TranscriptUploadForm
from .models import SpeechModel, TranscriptAccessMode, TranscriptCollection, TranscriptEvent, TranscriptSource, TranscriptionJob
from .queries import jobs_by_day_chart_data, source_stats, top_sources_chart_data, transcription_overview_stats
from .services import (
    activate_speech_model,
    can_access_source,
    cancel_job as cancel_transcription_job,
    celery_workers_available,
    create_transcription_job,
    export_transcript,
    readable_collections_for_user,
    readable_sources_for_user,
    record_event,
    run_transcription_with_timeout,
    start_speech_model_download,
    transcribe_demo_file,
    user_can_create_collections,
    user_is_transcription_manager,
)


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def home(request):
    sources = readable_sources_for_user(request.user).order_by("-created_at")[:24]
    stats = transcription_overview_stats()
    jobs_chart = jobs_by_day_chart_data(30)
    top_chart = top_sources_chart_data(8)
    return _render(request, "transcription/home.html", {"recent_sources": sources, "stats": stats, "jobs_chart": jobs_chart, "top_chart": top_chart})


def collection_list(request):
    qs = readable_collections_for_user(request.user)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(slug__icontains=q) | Q(description__icontains=q))
    qs = qs.annotate(source_count=Count("sources", filter=Q(sources__status=TranscriptSource.Status.TRANSCRIBED)))
    return _render(request, "transcription/collection_list.html", {"collections": qs[:200], "q": q, "stats": transcription_overview_stats(), "can_create": user_can_create_collections(request.user)})


@login_required
def collection_create(request):
    if not user_can_create_collections(request.user):
        return HttpResponseForbidden(_("You cannot create transcription collections."))
    if request.method == "POST":
        form = TranscriptCollectionForm(request.POST)
        if form.is_valid():
            collection = form.save(commit=False)
            collection.owner = request.user
            collection.save()
            form.save_m2m()
            messages.success(request, _("Transcript collection created."))
            return redirect(collection.get_absolute_url())
    else:
        form = TranscriptCollectionForm()
    return _render(request, "transcription/form.html", {"form": form, "action": _("Create Collection"), "back_url": reverse("transcription:collection_list")})


def collection_detail(request, slug):
    collection = get_object_or_404(TranscriptCollection.objects.select_related("owner"), slug=slug)
    if not collection.user_can_read(request.user):
        return HttpResponseForbidden(_("This collection is private."))
    sources = collection.sources.select_related("source_file", "collection").order_by("position", "title")
    can_manage = user_is_transcription_manager(request.user, collection)
    if not can_manage:
        sources = sources.filter(status=TranscriptSource.Status.TRANSCRIBED)
    return _render(request, "transcription/collection_detail.html", {"collection": collection, "sources": sources[:300], "can_manage": can_manage})


@login_required
def upload(request):
    if request.method == "POST":
        form = TranscriptUploadForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            source = form.save()
            messages.success(request, _("Media uploaded."))
            return redirect(source.get_manage_url())
    else:
        form = TranscriptUploadForm(user=request.user, initial={"collection": request.GET.get("collection")})
    if not form.fields["collection"].queryset.exists():
        messages.warning(request, _("Create a collection or ask for writer access before uploading."))
    return _render(request, "transcription/upload.html", {"form": form, "action": _("Upload Media"), "back_url": reverse("transcription:home")})


def source_detail(request, collection_slug, source_slug):
    source = get_object_or_404(TranscriptSource.objects.select_related("collection", "source_file"), collection__slug=collection_slug, slug=source_slug)
    decision = can_access_source(request.user, source)
    if decision.allowed:
        record_event(request=request, source=source, event=TranscriptEvent.EventKind.IMPRESSION)
    can_manage = user_is_transcription_manager(request.user, source.collection)
    related = source.collection.sources.filter(status=TranscriptSource.Status.TRANSCRIBED).exclude(pk=source.pk).select_related("source_file").order_by("position", "title")[:8]
    return _render(request, "transcription/source_detail.html", {"source": source, "collection": source.collection, "access": decision, "can_manage": can_manage, "related_sources": related})


@login_required
def source_manage(request, collection_slug, source_slug):
    source = get_object_or_404(TranscriptSource.objects.select_related("collection", "source_file"), collection__slug=collection_slug, slug=source_slug)
    if not user_is_transcription_manager(request.user, source.collection):
        return HttpResponseForbidden(_("Only collection writers can manage this source."))
    stats = source_stats(source)
    job_form = TranscriptionJobForm(initial={"language": source.language})
    return _render(request, "transcription/source_manage.html", {"source": source, "collection": source.collection, "stats": stats, "job_form": job_form, "jobs": source.jobs.all()[:20]})


@login_required
def source_edit(request, collection_slug, source_slug):
    source = get_object_or_404(TranscriptSource.objects.select_related("collection"), collection__slug=collection_slug, slug=source_slug)
    if not user_is_transcription_manager(request.user, source.collection):
        return HttpResponseForbidden(_("Only collection writers can edit this source."))
    if request.method == "POST":
        form = TranscriptSourceForm(request.POST, instance=source)
        if form.is_valid():
            form.save()
            messages.success(request, _("Source updated."))
            return redirect(source.get_manage_url())
    else:
        form = TranscriptSourceForm(instance=source)
    return _render(request, "transcription/form.html", {"form": form, "action": _("Edit Source"), "back_url": source.get_manage_url()})


@login_required
@require_POST
def start_transcription(request, collection_slug, source_slug):
    source = get_object_or_404(TranscriptSource.objects.select_related("collection", "source_file"), collection__slug=collection_slug, slug=source_slug)
    if not user_is_transcription_manager(request.user, source.collection):
        return HttpResponseForbidden(_("Only collection writers can transcribe this source."))
    form = TranscriptionJobForm(request.POST)
    if not form.is_valid():
        messages.error(request, form.errors.as_text())
        return redirect(source.get_manage_url())
    run_async = form.cleaned_data.get("run_async")
    if run_async and not celery_workers_available():
        messages.error(request, _("No Celery workers are running. Run synchronously or start a worker first."))
        return redirect(source.get_manage_url())
    # ── Tariff balance check before transcription ────────────────────────────
    _transcription_tariff = None
    try:
        from toto.tariffs.charge import (
            InsufficientBalanceError, check_user_can_act, get_tariff_for_user,
        )
        _transcription_tariff = get_tariff_for_user(request.user, "transcription")
        if _transcription_tariff:
            # estimate duration in minutes; default 1 if unknown
            _duration_min = max(1, (source.duration_seconds or 60) // 60)
            check_user_can_act(request.user, _transcription_tariff,
                               "transcription.minutes", _duration_min, unit="minutes")
    except InsufficientBalanceError as _exc:
        messages.error(request, str(_exc))
        return redirect(source.get_manage_url())
    except Exception:
        _transcription_tariff = None

    job = create_transcription_job(
        source=source,
        user=request.user,
        engine=form.cleaned_data["engine"],
        language=form.cleaned_data.get("language") or "",
        detect_speakers=form.cleaned_data.get("detect_speakers") or False,
        prompt=form.cleaned_data.get("prompt") or "",
    )
    record_event(request=request, source=source, event=TranscriptEvent.EventKind.TRANSCRIBE)

    # ── Charge after job created (actual minutes from source duration) ────────
    try:
        if _transcription_tariff:
            from toto.tariffs.charge import charge_user as _charge
            _actual_min = max(1, (source.duration_seconds or 60) // 60)
            _charge(request.user, _transcription_tariff, "transcription.minutes", _actual_min,
                    unit="minutes", source_type="transcription.TranscriptionJob", source_id=str(job.pk))
    except Exception:
        pass

    if run_async:
        from .tasks import run_transcription_job
        result = run_transcription_job.delay(job.pk)
        job.celery_task_id = result.id
        job.save(update_fields=["celery_task_id"])
        messages.success(request, _("Transcription job queued."))
    else:
        timeout = form.cleaned_data.get("timeout_seconds") or 0
        try:
            if timeout:
                run_transcription_with_timeout(job, timeout)
            else:
                from .services import run_transcription
                run_transcription(job)
            messages.success(request, _("Transcription completed."))
        except TimeoutError as exc:
            messages.error(request, str(exc))
        except Exception as exc:
            messages.error(request, str(exc))
    return redirect(source.get_manage_url())


@login_required
@require_POST
def job_cancel(request, collection_slug, source_slug, job_pk):
    source = get_object_or_404(TranscriptSource.objects.select_related("collection"), collection__slug=collection_slug, slug=source_slug)
    if not user_is_transcription_manager(request.user, source.collection):
        return HttpResponseForbidden(_("Only collection writers can cancel jobs."))
    try:
        cancel_transcription_job(job_pk, user=request.user)
        messages.success(request, _("Job cancelled."))
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect(source.get_manage_url())


def source_export(request, collection_slug, source_slug, kind):
    source = get_object_or_404(TranscriptSource.objects.select_related("collection", "source_file"), collection__slug=collection_slug, slug=source_slug)
    decision = can_access_source(request.user, source)
    if not decision.allowed:
        return HttpResponseForbidden(_("You cannot export this transcript."))
    if not source.collection.allow_downloads and not user_is_transcription_manager(request.user, source.collection):
        return HttpResponseForbidden(_("Downloads are disabled for this collection."))
    content, content_type, filename = export_transcript(source, kind)
    record_event(request=request, source=source, event=TranscriptEvent.EventKind.EXPORT, metadata={"kind": kind})
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_POST
def source_event_api(request, collection_slug, source_slug):
    source = get_object_or_404(TranscriptSource.objects.select_related("collection"), collection__slug=collection_slug, slug=source_slug)
    decision = can_access_source(request.user, source)
    if not decision.allowed:
        return JsonResponse({"ok": False, "reason": decision.reason}, status=403)
    event = request.POST.get("event") or TranscriptEvent.EventKind.PLAY
    seconds = request.POST.get("seconds_played") or request.POST.get("seconds") or 0
    playback = record_event(request=request, source=source, event=event, seconds_played=int(float(seconds)))
    return JsonResponse({"ok": True, "event_uid": str(playback.uid)})


# ---------------------------------------------------------------------------
# Model setup
# ---------------------------------------------------------------------------

@login_required
def model_setup(request):
    if not request.user.is_staff and not request.user.is_superuser:
        return HttpResponseForbidden(_("Model management is restricted to staff."))
    models_qs = SpeechModel.objects.order_by("backend", "name")
    return _render(request, "transcription/model_setup.html", {
        "speech_models": models_qs,
        "download_form": SpeechModelDownloadForm(),
        "celery_ok": celery_workers_available(),
    })


@login_required
def model_download(request, pk):
    if not request.user.is_staff and not request.user.is_superuser:
        return HttpResponseForbidden(_("Model management is restricted to staff."))
    m = get_object_or_404(SpeechModel, pk=pk)
    if request.method == "POST":
        form = SpeechModelDownloadForm(request.POST)
        if form.is_valid():
            try:
                start_speech_model_download(m.pk, form.cleaned_data["download_source"])
                messages.success(request, _("Download queued — watch the progress bar."))
            except Exception as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, form.errors.as_text())
        return redirect(reverse("transcription:model_setup"))
    form = SpeechModelDownloadForm(initial={"download_source": m.download_source})
    return _render(request, "transcription/model_download.html", {"speech_model": m, "form": form})


@login_required
def model_progress_api(request, pk):
    m = get_object_or_404(SpeechModel, pk=pk)
    return JsonResponse({
        "pk": m.pk,
        "download_status": m.download_status,
        "download_status_display": m.get_download_status_display(),
        "download_progress": m.download_progress,
        "is_active": m.is_active,
        "download_error": m.download_error,
    })


@login_required
@require_POST
def model_activate(request, pk):
    if not request.user.is_staff and not request.user.is_superuser:
        return HttpResponseForbidden(_("Model management is restricted to staff."))
    m = get_object_or_404(SpeechModel, pk=pk)
    try:
        activate_speech_model(m.pk)
        messages.success(request, _("Model set as active — it will be used for all default transcription jobs."))
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect(reverse("transcription:model_setup"))


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

_DEMO_MAX_BYTES = 10 * 1024 * 1024  # 10 MB — ~15 s audio is well under this
_DEMO_MAX_SECONDS = 15


def demo(request):
    if request.method == "POST":
        audio = request.FILES.get("audio")
        if not audio:
            return JsonResponse({"ok": False, "error": _("No audio file received.")}, status=400)
        if audio.size > _DEMO_MAX_BYTES:
            return JsonResponse({"ok": False, "error": _("Recording too large (max 10 MB).")}, status=400)
        engine = request.POST.get("engine") or TranscriptionJob.Engine.DEFAULT
        language = (request.POST.get("language") or "").strip()
        suffix = os.path.splitext(audio.name)[1] or ".webm"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            for chunk in audio.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name
        try:
            result = transcribe_demo_file(tmp_path, engine=engine, language=language)
        except Exception as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return JsonResponse({"ok": True, "text": result["text"], "segments": result["segments"]})

    engines = TranscriptionJob.Engine.choices
    return _render(request, "transcription/demo.html", {"engines": engines, "max_seconds": _DEMO_MAX_SECONDS})
