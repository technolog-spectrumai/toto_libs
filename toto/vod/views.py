from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .forms import VodCollectionForm, VodUploadForm, VodVideoForm
from .models import VodAccessMode, VodCollection, VodPlaybackEvent, VodVideo
from .services import build_hls_for_video, can_access_video, create_invoice_for_video_access, record_playback_event, user_is_vod_manager


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def collection_list(request):
    qs = VodCollection.objects.select_related("owner", "bucket", "required_plan")
    if not user_is_vod_manager(request.user):
        qs = qs.filter(access_mode=VodAccessMode.PUBLIC)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(slug__icontains=q) | Q(description__icontains=q))
    stats = {
        "collections": VodCollection.objects.count(),
        "published": VodVideo.objects.filter(status=VodVideo.Status.PUBLISHED).count(),
        "hls_ready": VodVideo.objects.filter(hls_ready=True).count(),
        "plays": VodPlaybackEvent.objects.filter(event=VodPlaybackEvent.EventKind.PLAY).count(),
    }
    return _render(request, "vod/collection_list.html", {"collections": qs[:200], "q": q, "stats": stats})


def collection_detail(request, slug):
    collection = get_object_or_404(VodCollection.objects.select_related("owner", "bucket", "required_plan"), slug=slug)
    if collection.access_mode == VodAccessMode.STAFF and not user_is_vod_manager(request.user, collection):
        return HttpResponseForbidden(_("This VOD collection is staff-only."))

    videos = collection.videos.select_related("source_file", "poster_file", "required_plan").order_by("position", "title")
    if not user_is_vod_manager(request.user, collection):
        videos = videos.filter(status=VodVideo.Status.PUBLISHED).exclude(access_mode=VodAccessMode.STAFF)
        if collection.access_mode == VodAccessMode.PUBLIC:
            videos = videos.exclude(access_mode=VodAccessMode.UNLISTED)
    return _render(request, "vod/collection_detail.html", {"collection": collection, "videos": videos[:300], "can_manage": user_is_vod_manager(request.user, collection)})


def video_detail(request, collection_slug, video_slug):
    video = get_object_or_404(
        VodVideo.objects.select_related("collection", "source_file", "poster_file", "required_plan", "collection__required_plan"),
        collection__slug=collection_slug,
        slug=video_slug,
    )
    decision = can_access_video(request.user, video)
    if decision.allowed:
        record_playback_event(request=request, video=video, event=VodPlaybackEvent.EventKind.IMPRESSION)
    return _render(request, "vod/video_detail.html", {"video": video, "collection": video.collection, "access": decision, "can_manage": user_is_vod_manager(request.user, video.collection)})


@login_required
def collection_create(request):
    if not user_is_vod_manager(request.user):
        return HttpResponseForbidden(_("Only staff can create VOD collections."))
    if request.method == "POST":
        form = VodCollectionForm(request.POST)
        if form.is_valid():
            collection = form.save()
            messages.success(request, _("VOD collection created."))
            return redirect(collection.get_absolute_url())
    else:
        form = VodCollectionForm(initial={"owner": request.user})
    return _render(request, "vod/form.html", {"form": form, "action": _("Create VOD Collection")})


@login_required
def video_edit(request, collection_slug, video_slug):
    video = get_object_or_404(VodVideo.objects.select_related("collection"), collection__slug=collection_slug, slug=video_slug)
    if not user_is_vod_manager(request.user, video.collection):
        return HttpResponseForbidden(_("Only staff or the collection owner can edit this video."))
    if request.method == "POST":
        form = VodVideoForm(request.POST, instance=video)
        if form.is_valid():
            form.save()
            messages.success(request, _("VOD video updated."))
            return redirect(video.get_absolute_url())
    else:
        form = VodVideoForm(instance=video)
    return _render(request, "vod/form.html", {"form": form, "action": _("Edit VOD Video")})


@login_required
def upload(request):
    if not user_is_vod_manager(request.user):
        return HttpResponseForbidden(_("Only staff can upload VOD source files."))
    if request.method == "POST":
        form = VodUploadForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            video = form.save()
            messages.success(request, _("VOD video uploaded."))
            return redirect(video.get_absolute_url())
    else:
        form = VodUploadForm(user=request.user)
    return _render(request, "vod/upload.html", {"form": form, "action": _("Upload VOD Video")})


@login_required
@require_POST
def build_hls(request, collection_slug, video_slug):
    video = get_object_or_404(VodVideo.objects.select_related("collection", "source_file"), collection__slug=collection_slug, slug=video_slug)
    if not user_is_vod_manager(request.user, video.collection):
        return HttpResponseForbidden(_("Only staff or the collection owner can build HLS."))
    try:
        build_hls_for_video(video, force=request.POST.get("force") == "1")
        messages.success(request, _("HLS playlist and TS segments built."))
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect(video.get_absolute_url())


@login_required
@require_POST
def request_invoice_access(request, collection_slug, video_slug):
    video = get_object_or_404(VodVideo.objects.select_related("collection", "source_file"), collection__slug=collection_slug, slug=video_slug)
    try:
        grant = create_invoice_for_video_access(user=request.user, video=video, issued_by=video.collection.owner)
        messages.success(request, _("Invoice created. Pay it in the invoices app to unlock this VOD."))
        invoice = grant.invoice
        if hasattr(invoice, "pk"):
            # The uploaded invoices app uses integer pk, not uid, so keep this conservative.
            return redirect(video.get_absolute_url())
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect(video.get_absolute_url())


@require_POST
def playback_event_api(request, collection_slug, video_slug):
    video = get_object_or_404(VodVideo.objects.select_related("collection", "required_plan", "collection__required_plan"), collection__slug=collection_slug, slug=video_slug)
    decision = can_access_video(request.user, video)
    if not decision.allowed:
        return JsonResponse({"ok": False, "reason": decision.reason}, status=403)
    event = request.POST.get("event") or VodPlaybackEvent.EventKind.PLAY
    seconds = request.POST.get("seconds_watched") or request.POST.get("seconds") or 0
    playback = record_playback_event(request=request, video=video, event=event, seconds_watched=int(float(seconds)))
    return JsonResponse({"ok": True, "event_uid": str(playback.uid), "subscription_usage": bool(playback.subscription_usage_id)})
