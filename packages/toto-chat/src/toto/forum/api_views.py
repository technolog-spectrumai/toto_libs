import json
from urllib.parse import urlparse

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.db import models

from toto.api.cors import CorsApiView
from toto.forum import permissions, store
from toto.forum.models import ForumChannel


def _channel_to_dict(channel, member_count=None):
    return {
        "id": channel.id,
        "name": channel.name,
        "slug": channel.slug,
        "member_count": member_count if member_count is not None else 0,
        "created_at": channel.created_at.isoformat(),
        "access": channel.access,
        "is_encrypted": channel.is_encrypted,
        "expires_at": channel.expires_at.isoformat() if channel.expires_at else None,
    }


def _guard(request):
    """The Fetch-Metadata refusal (toto.api.fetch_metadata), or None."""
    from toto.api.fetch_metadata import cross_site_refusal

    return cross_site_refusal(request)


def _refusal(exc):
    status = getattr(exc, "status", None) or getattr(exc, "status_code", 400)
    return JsonResponse({"error": str(exc)}, status=status)


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
        qs = permissions.listable_channels(request.user).annotate(
            member_count=models.Count(
                "forum_members",
                filter=models.Q(forum_members__is_active=True),
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
        from toto.quota.api import InArrears, QuotaExceeded
        from toto.quota.charge import InsufficientFunds

        from . import creation

        refused = _guard(request)
        if refused is not None:
            return refused
        if not permissions.can_browse(request.user):
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)
        try:
            channel = creation.create_room(
                request.user, name=body.get("name", ""), access=body.get("access", "open"),
                password=body.get("password", ""), encrypted=bool(body.get("is_encrypted")),
                expires_in=body.get("expires_in", "") or "")
        except creation.RoomRefused as exc:
            return _refusal(exc)
        except (QuotaExceeded, InArrears, InsufficientFunds) as exc:
            return _refusal(exc)
        return JsonResponse(_channel_to_dict(channel, channel.forum_members.count()), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class ChannelDetailApiView(CorsApiView):
    def get(self, request, slug):
        if not permissions.can_browse(request.user):
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            channel = ForumChannel.objects.get(slug=slug)
        except ForumChannel.DoesNotExist:
            return JsonResponse({"error": "Channel not found."}, status=404)

        members_qs = channel.forum_members.filter(is_active=True).select_related(
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
    """`GET /forum/api/channels/<slug>/messages/?before=&limit=`

    Paginated history for the load-older affordance. ``before`` is an ISO-8601
    ``created_at`` cursor — pass the oldest message you already hold.
    """

    def get(self, request, slug):
        from django.utils.dateparse import parse_datetime

        from . import store

        # Authenticate BEFORE resolving the channel: otherwise 403-vs-404 tells an
        # anonymous caller exactly which private channel slugs exist.
        if not permissions.can_browse(request.user):
            return JsonResponse({"error": "Not authenticated."}, status=401)

        try:
            channel = ForumChannel.objects.get(slug=slug)
        except ForumChannel.DoesNotExist:
            return JsonResponse({"error": "Channel not found."}, status=404)

        if not permissions.can_read(request.user, channel):
            return JsonResponse({"error": "Join this channel to read its history."}, status=403)

        before = None
        raw_before = request.GET.get("before")
        if raw_before:
            before = parse_datetime(raw_before)
            if before is None:
                return JsonResponse({"error": "Invalid 'before' timestamp."}, status=400)

        raw_limit = request.GET.get("limit")
        if raw_limit:
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                return JsonResponse({"error": "Invalid 'limit'."}, status=400)
        else:
            limit = store.DEFAULT_HISTORY_LIMIT

        before_id = request.GET.get("before_id") or None

        messages = store.history(
            channel,
            limit=limit,
            before=before,
            before_id=before_id,
            absolute=lambda url: _absolute_url(request, url),
        )
        oldest = messages[0] if messages else None
        return JsonResponse({
            "messages": messages,
            "has_more": (
                store.has_more_before(
                    channel, parse_datetime(oldest["created_at"]), oldest["id"]
                )
                if oldest else False
            ),
        })


@method_decorator(csrf_exempt, name="dispatch")
class MessageAttachmentApiView(CorsApiView):
    """`GET /forum/api/messages/<uuid>/attachment/` — the only way to read an attachment.

    Attachments are stored outside ``MEDIA_ROOT`` precisely so that nginx cannot hand
    them out unauthenticated; this view applies the same membership rule as the message
    the file belongs to.
    """

    def get(self, request, message_id):
        from django.http import FileResponse, Http404

        from .models import ForumMessage

        if not permissions.can_browse(request.user):
            return JsonResponse({"error": "Not authenticated."}, status=401)

        row = (
            ForumMessage.objects.select_related("channel")
            .filter(id=message_id, deleted_at__isnull=True)
            .first()
        )
        if not row or not row.attachment:
            raise Http404("No such attachment.")

        if not permissions.can_read(request.user, row.channel):
            return JsonResponse(
                {"error": "Join this channel to read its attachments."}, status=403
            )

        if row.attachment_sealed:
            from django.http import HttpResponse

            from . import sealing
            from .rooms import RoomKeyUnavailable, open_key

            try:
                with row.attachment.open("rb") as fh:
                    frame = fh.read()
                data = sealing.open_bytes(open_key(row.channel), frame, kind="att",
                                          channel_id=row.channel_id, message_id=row.id)
            except (FileNotFoundError, OSError):
                raise Http404("Attachment file is missing.")
            except (RoomKeyUnavailable, sealing.SealBroken):
                return JsonResponse({"error": "This attachment cannot be opened now."}, status=409)
            response = HttpResponse(data, content_type=row.attachment_mime or "application/octet-stream")
            response["Content-Disposition"] = f'inline; filename="{row.attachment_name or "attachment"}"'
            response["X-Content-Type-Options"] = "nosniff"
            return response

        try:
            handle = row.attachment.open("rb")
        except (FileNotFoundError, OSError):
            raise Http404("Attachment file is missing.")

        return FileResponse(
            handle,
            content_type=row.attachment_mime or "application/octet-stream",
            filename=row.attachment_name or "attachment",
        )


@method_decorator(csrf_exempt, name="dispatch")
class MessageSearchApiView(CorsApiView):
    """`GET /forum/api/search/?q=&channel=` — scoped to the requester's channels."""

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
        from . import creation

        refused = _guard(request)
        if refused is not None:
            return refused
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        channel = permissions.listable_channels(request.user).filter(slug=slug).first()
        if channel is None:
            return JsonResponse({"error": "Channel not found."}, status=404)
        password = ""
        if request.body:
            try:
                password = (json.loads(request.body) or {}).get("password", "")
            except (json.JSONDecodeError, ValueError, AttributeError):
                password = request.POST.get("password", "")
        try:
            created = creation.join(request.user, channel, password=password)
        except creation.RoomRefused as exc:
            return _refusal(exc)
        return JsonResponse({"ok": True, "joined": created})


@method_decorator(csrf_exempt, name="dispatch")
class ChannelLeaveApiView(CorsApiView):
    def post(self, request, slug):
        refused = _guard(request)
        if refused is not None:
            return refused
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            channel = ForumChannel.objects.get(slug=slug)
        except ForumChannel.DoesNotExist:
            return JsonResponse({"error": "Channel not found."}, status=404)

        from toto.people.models import Person
        from toto.forum.models import ForumMember

        person = Person.objects.filter(user=request.user).first()
        if person:
            # Per instance, not a queryset .update(): only instance saves fire the
            # signal that tells an already-connected socket its member was revoked.
            for member in ForumMember.objects.filter(
                channel=channel, person=person, is_active=True
            ):
                member.is_active = False
                member.save(update_fields=["is_active"])

        return JsonResponse({"ok": True})


@method_decorator(csrf_exempt, name="dispatch")
class ChannelLeaveAllApiView(CorsApiView):
    def post(self, request):
        refused = _guard(request)
        if refused is not None:
            return refused
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        from toto.people.models import Person
        from toto.forum.models import ForumMember

        person = Person.objects.filter(user=request.user).first()
        if not person:
            return JsonResponse({"ok": True, "left": 0})

        left = ForumChannel.objects.filter(
            forum_members__person=person,
            forum_members__is_active=True,
        ).distinct().count()

        for member in ForumMember.objects.filter(person=person, is_active=True).select_related("channel"):
            member.is_active = False
            member.save(update_fields=["is_active"])

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
        refused = _guard(request)
        if refused is not None:
            return refused
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        try:
            channel = ForumChannel.objects.get(slug=slug)
        except ForumChannel.DoesNotExist:
            return JsonResponse({"error": "Channel not found."}, status=404)

        member = permissions.member_for(request.user, channel)
        if not member or channel.is_expired:
            return JsonResponse(
                {"error": "Join this channel before posting to it."}, status=403
            )

        file = request.FILES.get(self.form_field)
        if not file:
            return JsonResponse({"error": f"No {self.form_field} file provided."}, status=400)

        content_type = file.content_type or ""
        if not self._accepts(content_type):
            return JsonResponse({"error": self.error_label}, status=415)
        # The content type is the sender's word; the bytes are not. An Office
        # file declared as a PNG is still one (stage 34's review, 2026-10-01).
        from toto.vault.models import is_office_file, office_refusal_sentence
        if is_office_file(file.name or "", file):
            return JsonResponse({"error": office_refusal_sentence()}, status=415)

        if file.size > _MAX_MEDIA_BYTES:
            return JsonResponse(
                {"error": f"File too large. Maximum size is {_MAX_MEDIA_BYTES // (1024 * 1024)} MB."},
                status=413,
            )

        display_name = member.display_name
        avatar_url = _absolute_url(request, member.avatar_url)

        from django.db import transaction

        from toto.core import ratelimit
        from toto.quota.api import InArrears, QuotaExceeded
        from toto.quota.charge import InsufficientFunds

        from . import billing, creation
        from .rooms import RoomKeyUnavailable, open_key

        limit, window = creation.limits()["api_post"]
        try:
            ratelimit.check(f"forum:post:{request.user.pk}", limit=limit, window=window)
            billing.check_affordable(request.user, channel)
            key = open_key(channel) if channel.is_encrypted else None
            with transaction.atomic():
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
                    key=key,
                )
                billing.settle_message(request.user, row)
        except ratelimit.RateLimited as exc:
            return _refusal(exc)
        except (QuotaExceeded, InArrears, InsufficientFunds) as exc:
            return _refusal(exc)
        except RoomKeyUnavailable as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        payload = store.message_to_dict(
            row, absolute=lambda url: _absolute_url(request, url), key=key
        )

        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f"forum_{channel.slug}",
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
    error_label = _("Unsupported file type. Send a JPEG, PNG, GIF, or WebP.")

    def _accepts(self, content_type):
        return content_type in _ALLOWED_IMAGE_TYPES


class AudioUploadApiView(MediaUploadApiView):
    msg_type = "voice_message"
    form_field = "audio"
    error_label = _(
        "Unsupported file type. Send audio/webm, audio/ogg, audio/mp4, audio/wav, or audio/mpeg."
    )

    def _accepts(self, content_type):
        return any(content_type.startswith(p) for p in _ALLOWED_AUDIO_PREFIXES)
