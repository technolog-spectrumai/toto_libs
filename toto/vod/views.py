from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .forms import VodCollectionForm, VodUploadForm, VodVideoForm
from .models import VodAccessMode, VodCollection, VodPlaybackEvent, VodVideo
from .queries import plays_by_day_chart_data, top_videos_chart_data, video_stats, vod_overview_stats
from .services import (
    build_hls_for_video,
    can_access_video,
    record_playback_event,
    user_is_vod_manager,
)


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


# ---------------------------------------------------------------------------
# Home / landing view
# ---------------------------------------------------------------------------

def home(request):
    qs = VodVideo.objects.select_related("collection", "source_file", "poster_file")
    if not user_is_vod_manager(request.user):
        qs = qs.filter(status=VodVideo.Status.PUBLISHED).exclude(
            collection__access_mode=VodAccessMode.PRIVATE
        )
    recent = qs.order_by("-created_at")[:24]
    stats = vod_overview_stats()
    plays_chart = plays_by_day_chart_data(30)
    top_chart = top_videos_chart_data(8)
    return _render(request, "vod/home.html", {
        "recent_videos": recent,
        "stats": stats,
        "plays_chart": plays_chart,
        "top_chart": top_chart,
    })


# ---------------------------------------------------------------------------
# Collection views
# ---------------------------------------------------------------------------

def collection_list(request):
    qs = VodCollection.objects.select_related("owner")
    if not user_is_vod_manager(request.user):
        qs = qs.exclude(access_mode=VodAccessMode.PRIVATE)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(slug__icontains=q) | Q(description__icontains=q))
    qs = qs.annotate(video_count=Count("videos", filter=Q(videos__status=VodVideo.Status.PUBLISHED)))
    from toto.quota import usage_summary
    quota_data = usage_summary("vod", "auth.User", str(request.user.pk)) if request.user.is_authenticated else []
    return _render(request, "vod/collection_list.html", {"collections": qs[:200], "q": q, "quota_data": quota_data})


def collection_detail(request, slug):
    collection = get_object_or_404(VodCollection.objects.select_related("owner"), slug=slug)
    if collection.access_mode == VodAccessMode.PRIVATE and not user_is_vod_manager(request.user, collection):
        return HttpResponseForbidden(_("This collection is private."))

    videos = collection.videos.select_related("source_file", "poster_file").order_by("position", "title")
    if not user_is_vod_manager(request.user, collection):
        videos = videos.filter(status=VodVideo.Status.PUBLISHED)
    return _render(request, "vod/collection_detail.html", {
        "collection": collection,
        "videos": videos[:300],
        "can_manage": user_is_vod_manager(request.user, collection),
    })


@login_required
def collection_create(request):
    if not user_is_vod_manager(request.user):
        return HttpResponseForbidden(_("Only the owner or superuser can create VOD collections."))
    if request.method == "POST":
        form = VodCollectionForm(request.POST)
        if form.is_valid():
            collection = form.save(commit=False)
            collection.owner = request.user
            collection.save()
            messages.success(request, _("VOD collection created."))
            return redirect(collection.get_absolute_url())
    else:
        form = VodCollectionForm()
    return _render(request, "vod/form.html", {"form": form, "action": _("Create Collection"), "back_url": reverse("vod:collection_list")})


# ---------------------------------------------------------------------------
# Video viewer (clean watch page)
# ---------------------------------------------------------------------------

def video_detail(request, collection_slug, video_slug):
    video = get_object_or_404(
        VodVideo.objects.select_related("collection", "source_file", "poster_file"),
        collection__slug=collection_slug,
        slug=video_slug,
    )
    decision = can_access_video(request.user, video)
    if decision.allowed:
        record_playback_event(request=request, video=video, event=VodPlaybackEvent.EventKind.IMPRESSION)
    can_manage = user_is_vod_manager(request.user, video.collection)
    # Related videos from same collection
    related = (
        video.collection.videos
        .filter(status=VodVideo.Status.PUBLISHED)
        .exclude(pk=video.pk)
        .select_related("poster_file")
        .order_by("position", "title")[:8]
    )
    return _render(request, "vod/video_detail.html", {
        "video": video,
        "collection": video.collection,
        "access": decision,
        "can_manage": can_manage,
        "related_videos": related,
    })


# ---------------------------------------------------------------------------
# Video manager view (owner / superuser only)
# ---------------------------------------------------------------------------

@login_required
def video_manage(request, collection_slug, video_slug):
    video = get_object_or_404(
        VodVideo.objects.select_related("collection", "source_file", "poster_file"),
        collection__slug=collection_slug,
        slug=video_slug,
    )
    if not user_is_vod_manager(request.user, video.collection):
        return HttpResponseForbidden(_("Only the collection owner or superuser can manage this video."))
    stats = video_stats(video)
    return _render(request, "vod/video_manage.html", {
        "video": video,
        "collection": video.collection,
        "stats": stats,
    })


# ---------------------------------------------------------------------------
# Edit / Upload
# ---------------------------------------------------------------------------

@login_required
def video_edit(request, collection_slug, video_slug):
    video = get_object_or_404(VodVideo.objects.select_related("collection"), collection__slug=collection_slug, slug=video_slug)
    if not user_is_vod_manager(request.user, video.collection):
        return HttpResponseForbidden(_("Only the collection owner or superuser can edit this video."))
    if request.method == "POST":
        form = VodVideoForm(request.POST, instance=video)
        if form.is_valid():
            form.save()
            messages.success(request, _("Video updated."))
            return redirect(video.get_absolute_url())
    else:
        form = VodVideoForm(instance=video)
    return _render(request, "vod/form.html", {"form": form, "action": _("Edit Video"), "back_url": video.get_absolute_url()})


@login_required
def upload(request):
    if not user_is_vod_manager(request.user):
        return HttpResponseForbidden(_("Only the collection owner or superuser can upload videos."))
    if request.method == "POST":
        form = VodUploadForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            video = form.save()
            messages.success(request, _("Video uploaded."))
            return redirect(video.get_absolute_url())
    else:
        form = VodUploadForm(user=request.user)
    return _render(request, "vod/upload.html", {"form": form, "action": _("Upload Video"), "back_url": reverse("vod:home")})


# ---------------------------------------------------------------------------
# HLS + invoice + analytics API
# ---------------------------------------------------------------------------

@login_required
@require_POST
def build_hls(request, collection_slug, video_slug):
    video = get_object_or_404(VodVideo.objects.select_related("collection", "source_file"), collection__slug=collection_slug, slug=video_slug)
    if not user_is_vod_manager(request.user, video.collection):
        return HttpResponseForbidden(_("Only the collection owner or superuser can build HLS."))
    try:
        build_hls_for_video(video, force=request.POST.get("force") == "1")
        messages.success(request, _("HLS playlist built."))
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("vod:video_manage", collection_slug=video.collection.slug, video_slug=video.slug)


@require_POST
def playback_event_api(request, collection_slug, video_slug):
    video = get_object_or_404(
        VodVideo.objects.select_related("collection"),
        collection__slug=collection_slug,
        slug=video_slug,
    )
    decision = can_access_video(request.user, video)
    if not decision.allowed:
        return JsonResponse({"ok": False, "reason": decision.reason}, status=403)

    # VOD access is controlled by subscriptions (Phase 4), not per-stream tariffs.
    # Storage of video files is metered by vault (storage.request / storage.transfer_mb).
    event = request.POST.get("event") or VodPlaybackEvent.EventKind.PLAY
    seconds = request.POST.get("seconds_watched") or request.POST.get("seconds") or 0
    playback = record_playback_event(request=request, video=video, event=event, seconds_watched=int(float(seconds)))
    return JsonResponse({"ok": True, "event_uid": str(playback.uid)})
