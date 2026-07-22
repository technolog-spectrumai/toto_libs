import json
from urllib.parse import urlparse

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import models

from toto.api.cors import CorsApiView
from toto.telegraph import permissions, store
from toto.telegraph.models import TelegraphChannel


def _channel_to_dict(channel, member_count=None):
    return {
        "id": channel.id,
        "name": channel.name,
        "slug": channel.slug,
        "member_count": member_count if member_count is not None else 0,
        "created_at": channel.created_at.isoformat(),
    }


def _absolute_url(request, url):
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return url
    return request.build_absolute_uri(url)


def _member_username(member):
    """The member's SSO username (for aster NodeId resolution), or "" if the
    person has no linked account."""
    person = getattr(member, "person", None)
    if person and getattr(person, "user_id", None):
        return person.user.username
    return ""


def _member_to_dict(request, member):
    return {
        "name": member.display_name,
        "username": _member_username(member),
        "avatar_url": _absolute_url(request, member.avatar_url),
        "type": member.participant_type,
    }


@method_decorator(csrf_exempt, name="dispatch")
class ChannelListApiView(CorsApiView):
    """`GET` the channel list · `POST` to create one.

    Both require authentication: messages are permanent now, so the roster and channel
    names are no longer disclosed anonymously.
    """

    def get(self, request):
        if not permissions.can_browse(request.user):
            return JsonResponse({"error": "Not authenticated."}, status=401)

        joined = set(permissions.readable_channels(request.user).values_list("pk", flat=True))
        qs = TelegraphChannel.objects.annotate(
            member_count=models.Count(
                "telegraph_members",
                filter=models.Q(telegraph_members__is_active=True),
                distinct=True,
            )
        ).order_by("name")
        channels = []
        for channel in qs:
            data = _channel_to_dict(channel, channel.member_count)
            data["is_member"] = channel.pk in joined
            channels.append(data)
        return JsonResponse({"channels": channels})

    def post(self, request):
        from django.utils.text import slugify

        from toto.people.models import Person
        from toto.telegraph.models import TelegraphMember

        if not permissions.can_browse(request.user):
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        name = (body.get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "Channel name required."}, status=400)
        slug = slugify(name)[:50]
        if not slug:
            return JsonResponse({"error": "Name cannot be turned into a slug."}, status=400)
        if TelegraphChannel.objects.filter(models.Q(name=name) | models.Q(slug=slug)).exists():
            return JsonResponse({"error": "That channel already exists."}, status=409)

        channel = TelegraphChannel.objects.create(
            name=name, slug=slug, created_by=request.user
        )
        person = Person.objects.filter(user=request.user).first()
        if person:
            TelegraphMember.objects.create(channel=channel, person=person, is_active=True)

        return JsonResponse(_channel_to_dict(channel, 1 if person else 0), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class ChannelDetailApiView(CorsApiView):
    def get(self, request, slug):
        if not permissions.can_browse(request.user):
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            channel = TelegraphChannel.objects.get(slug=slug)
        except TelegraphChannel.DoesNotExist:
            return JsonResponse({"error": "Channel not found."}, status=404)

        members_qs = channel.telegraph_members.filter(is_active=True).select_related(
            "person", "person__user"
        )
        is_member = permissions.is_member(request.user, channel)

        data = _channel_to_dict(channel, members_qs.count())
        # The roster is members-only; non-members still see that the channel exists so
        # they can join it.
        data["members"] = [_member_to_dict(request, m) for m in members_qs] if is_member else []
        data["is_member"] = is_member
        return JsonResponse(data)


@method_decorator(csrf_exempt, name="dispatch")
class ChannelMessagesApiView(CorsApiView):
    """`GET /telegraph/api/channels/<slug>/messages/?before=&limit=`

    Paginated history for the load-older affordance. ``before`` is an ISO-8601
    ``created_at`` cursor — pass the oldest message you already hold.
    """

    def get(self, request, slug):
        from django.utils.dateparse import parse_datetime

        from . import store

        try:
            channel = TelegraphChannel.objects.get(slug=slug)
        except TelegraphChannel.DoesNotExist:
            return JsonResponse({"error": "Channel not found."}, status=404)

        if not permissions.can_read(request.user, channel):
            return JsonResponse({"error": "Join this channel to read its history."}, status=403)

        before = None
        raw_before = request.GET.get("before")
        if raw_before:
            before = parse_datetime(raw_before)
            if before is None:
                return JsonResponse({"error": "Invalid 'before' timestamp."}, status=400)

        messages = store.history(
            channel,
            limit=request.GET.get("limit") or store.DEFAULT_HISTORY_LIMIT,
            before=before,
            absolute=lambda url: _absolute_url(request, url),
        )
        oldest = messages[0]["created_at"] if messages else None
        return JsonResponse({
            "messages": messages,
            "has_more": store.has_more_before(channel, oldest) if oldest else False,
        })


@method_decorator(csrf_exempt, name="dispatch")
class MessageSearchApiView(CorsApiView):
    """`GET /telegraph/api/search/?q=&channel=` — scoped to the requester's channels."""

    def get(self, request):
        from . import store
        from .search import search_messages, search_mode

        if not permissions.can_browse(request.user):
            return JsonResponse({"error": "Not authenticated."}, status=401)

        query = (request.GET.get("q") or "").strip()
        if not query:
            return JsonResponse({"results": [], "count": 0, **search_mode()})

        qs = search_messages(
            request.user, query, channel_slug=request.GET.get("channel") or None
        )
        rows = list(qs[:100])
        results = []
        for row in rows:
            payload = store.message_to_dict(
                row, absolute=lambda url: _absolute_url(request, url)
            )
            payload["channel_slug"] = row.channel.slug
            payload["channel_name"] = row.channel.name
            results.append(payload)
        return JsonResponse({"results": results, "count": len(results), **search_mode()})


@method_decorator(csrf_exempt, name="dispatch")
class ChannelJoinApiView(CorsApiView):
    def post(self, request, slug):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            channel = TelegraphChannel.objects.get(slug=slug)
        except TelegraphChannel.DoesNotExist:
            return JsonResponse({"error": "Channel not found."}, status=404)

        from toto.people.models import Person
        from toto.telegraph.models import TelegraphMember

        person = Person.objects.filter(user=request.user).first()
        if not person:
            return JsonResponse({"error": "No person profile linked to this account."}, status=403)

        member, created = TelegraphMember.objects.get_or_create(
            channel=channel, person=person, defaults={"is_active": True}
        )
        if not member.is_active:
            member.is_active = True
            member.save(update_fields=["is_active"])

        return JsonResponse({"ok": True, "joined": created})


@method_decorator(csrf_exempt, name="dispatch")
class ChannelLeaveApiView(CorsApiView):
    def post(self, request, slug):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            channel = TelegraphChannel.objects.get(slug=slug)
        except TelegraphChannel.DoesNotExist:
            return JsonResponse({"error": "Channel not found."}, status=404)

        from toto.people.models import Person
        from toto.telegraph.models import TelegraphMember

        person = Person.objects.filter(user=request.user).first()
        if person:
            TelegraphMember.objects.filter(channel=channel, person=person, is_active=True).update(is_active=False)

        return JsonResponse({"ok": True})


@method_decorator(csrf_exempt, name="dispatch")
class ChannelLeaveAllApiView(CorsApiView):
    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        from toto.people.models import Person
        from toto.telegraph.models import TelegraphMember

        person = Person.objects.filter(user=request.user).first()
        if not person:
            return JsonResponse({"ok": True, "left": 0})

        left = TelegraphChannel.objects.filter(
            telegraph_members__person=person,
            telegraph_members__is_active=True,
        ).distinct().count()

        TelegraphMember.objects.filter(person=person, is_active=True).update(is_active=False)

        return JsonResponse({"ok": True, "left": left})


_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_ALLOWED_AUDIO_PREFIXES = ("audio/webm", "audio/ogg", "audio/mp4", "audio/wav", "audio/mpeg")
_MAX_MEDIA_BYTES = 10 * 1024 * 1024  # 10 MB


@method_decorator(csrf_exempt, name="dispatch")
class MediaUploadApiView(CorsApiView):
    """Shared upload handler for the image and voice endpoints.

    The file is stored on disk via the message's ``FileField`` and the broadcast carries a
    URL. Media used to be inlined as a base64 ``data:`` URL inside the (encrypted) row — a
    10 MB upload became a ~13.4 MB blob that was replayed down the socket on every history
    load. That was survivable under a 24h TTL and is not survivable now that messages are
    permanent.
    """

    msg_type = None
    form_field = None
    error_label = None

    def _accepts(self, content_type):
        raise NotImplementedError

    def post(self, request, slug):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        try:
            channel = TelegraphChannel.objects.get(slug=slug)
        except TelegraphChannel.DoesNotExist:
            return JsonResponse({"error": "Channel not found."}, status=404)

        member = permissions.member_for(request.user, channel)
        if not member:
            return JsonResponse(
                {"error": "Join this channel before posting to it."}, status=403
            )

        file = request.FILES.get(self.form_field)
        if not file:
            return JsonResponse({"error": f"No {self.form_field} file provided."}, status=400)

        content_type = file.content_type or ""
        if not self._accepts(content_type):
            return JsonResponse({"error": self.error_label}, status=415)

        if file.size > _MAX_MEDIA_BYTES:
            return JsonResponse(
                {"error": f"File too large. Maximum size is {_MAX_MEDIA_BYTES // (1024 * 1024)} MB."},
                status=413,
            )

        display_name = member.display_name
        avatar_url = _absolute_url(request, member.avatar_url)

        row = store.store_message(
            channel,
            msg_type=self.msg_type,
            body=(request.POST.get("message") or "").strip(),
            sender=request.user,
            sender_name=display_name,
            sender_avatar_url=avatar_url or "",
            attachment=file,
            attachment_name=file.name or "",
            attachment_mime=content_type,
            attachment_size=file.size,
        )
        payload = store.message_to_dict(
            row, absolute=lambda url: _absolute_url(request, url)
        )

        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f"telegraph_{channel.slug}",
                {
                    "type": "chat_message",
                    "payload": payload,
                    "sender_channel": None,
                    "echo": True,
                },
            )

        return JsonResponse({"ok": True, **payload})


class ImageUploadApiView(MediaUploadApiView):
    msg_type = "image_message"
    form_field = "image"
    error_label = "Unsupported file type. Send a JPEG, PNG, GIF, or WebP."

    def _accepts(self, content_type):
        return content_type in _ALLOWED_IMAGE_TYPES


class AudioUploadApiView(MediaUploadApiView):
    msg_type = "voice_message"
    form_field = "audio"
    error_label = (
        "Unsupported file type. Send audio/webm, audio/ogg, audio/mp4, audio/wav, or audio/mpeg."
    )

    def _accepts(self, content_type):
        return any(content_type.startswith(p) for p in _ALLOWED_AUDIO_PREFIXES)
