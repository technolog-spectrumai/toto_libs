import base64
import json
from urllib.parse import urlparse

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import models
from toto.telegraph.models import TelegraphChannel

# CorsApiView / MeshGatedApiView and the data-mesh helpers now live in the
# always-on toto.api app so the basic (no-studio) tier can subclass them without
# pulling in channels. Re-exported here for backwards compatibility.
from toto.api.cors import (  # noqa: E402,F401
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    DATA_MESH_GROUP,
    MESH_DOMAINS,
    CorsApiView,
    MeshGatedApiView,
    _cors,
    _is_allowed_origin,
    _try_bearer_auth,
    in_data_mesh,
    mesh_required,
    render_access_denied,
)


@method_decorator(csrf_exempt, name="dispatch")
class MeshMeApiView(CorsApiView):
    """`GET /telegraph/api/me/mesh/` — whether the user can read gated data from the
    server (else the client must peer-pull)."""

    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        member = in_data_mesh(request.user)
        return JsonResponse({
            "member": member,
            "group": DATA_MESH_GROUP,
            "gated": MESH_DOMAINS,
            "allowed": MESH_DOMAINS if member else [],
        })


@method_decorator(csrf_exempt, name="dispatch")
class HealthApiView(CorsApiView):
    def get(self, request):
        return JsonResponse({"ok": True, "service": "telegraph"})


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
class LoginApiView(CorsApiView):
    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        username = body.get("username", "").strip()
        password = body.get("password", "")
        if not username or not password:
            return JsonResponse({"error": "Username and password required."}, status=400)

        user = authenticate(request, username=username, password=password)
        if user is None:
            return JsonResponse({"error": "Invalid credentials."}, status=401)

        login(request, user)
        return JsonResponse({"ok": True, "token": request.session.session_key})


@method_decorator(csrf_exempt, name="dispatch")
class LogoutApiView(CorsApiView):
    def post(self, request):
        logout(request)
        return JsonResponse({"ok": True})


def _supported_languages():
    """`{code: label}` of languages the platform offers (e.g. en, pl)."""
    from django.conf import settings
    return dict(settings.LANGUAGES)


@method_decorator(csrf_exempt, name="dispatch")
class MeApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        user = request.user
        data = {
            "id": user.id,
            "username": user.username,
            "full_name": user.get_full_name() or user.username,
            "avatar_url": None,
            "profile_url": None,
            "language": "en",
        }

        try:
            from toto.people.models import Person
            person = Person.objects.filter(user=user).first()
            if person:
                data["full_name"] = person.full_name or data["full_name"]
                data["avatar_url"] = request.build_absolute_uri(person.avatar.url) if person.avatar else None
                data["profile_url"] = f"/socialhub/profiles/{person.slug}/"
                data["language"] = person.preferred_language or "en"
        except Exception:
            pass

        return JsonResponse(data)

    def patch(self, request):
        """Persist a client's language choice onto the user's Person profile."""
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        language = str(body.get("language", "")).strip()
        if language not in _supported_languages():
            return JsonResponse({"error": "Unsupported language."}, status=400)

        stored = False
        try:
            from toto.people.models import Person
            person = Person.objects.filter(user=request.user).first()
            if person:
                person.preferred_language = language
                person.save(update_fields=["preferred_language"])
                stored = True
        except Exception:
            pass

        return JsonResponse({"ok": True, "language": language, "stored": stored})


@method_decorator(csrf_exempt, name="dispatch")
class ChannelListApiView(CorsApiView):
    def get(self, request):
        qs = TelegraphChannel.objects.annotate(
            member_count=models.Count(
                "telegraph_members",
                filter=models.Q(telegraph_members__is_active=True),
                distinct=True,
            )
        ).order_by("name")
        return JsonResponse({"channels": [_channel_to_dict(c, c.member_count) for c in qs]})


@method_decorator(csrf_exempt, name="dispatch")
class ChannelDetailApiView(CorsApiView):
    def get(self, request, slug):
        try:
            channel = TelegraphChannel.objects.get(slug=slug)
        except TelegraphChannel.DoesNotExist:
            return JsonResponse({"error": "Channel not found."}, status=404)

        members_qs = channel.telegraph_members.filter(is_active=True).select_related(
            "person", "person__user"
        )
        members = [_member_to_dict(request, m) for m in members_qs]

        is_member = False
        if request.user and request.user.is_authenticated:
            try:
                from toto.people.models import Person
                person = Person.objects.filter(user=request.user).first()
                if person:
                    is_member = channel.telegraph_members.filter(person=person, is_active=True).exists()
            except Exception:
                pass

        data = _channel_to_dict(channel, members_qs.count())
        data["members"] = members
        data["is_member"] = is_member
        return JsonResponse(data)


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

        channel.participants.add(request.user)
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
        channel.participants.remove(request.user)
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

        active_channels = TelegraphChannel.objects.filter(
            telegraph_members__person=person,
            telegraph_members__is_active=True,
        ).distinct()
        left = active_channels.count()

        for channel in active_channels:
            channel.participants.remove(request.user)

        TelegraphMember.objects.filter(person=person, is_active=True).update(is_active=False)

        return JsonResponse({"ok": True, "left": left})


_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_ALLOWED_AUDIO_PREFIXES = ("audio/webm", "audio/ogg", "audio/mp4", "audio/wav", "audio/mpeg")
_MAX_MEDIA_BYTES = 10 * 1024 * 1024  # 10 MB


@method_decorator(csrf_exempt, name="dispatch")
class ImageUploadApiView(CorsApiView):
    def post(self, request, slug):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        try:
            channel = TelegraphChannel.objects.get(slug=slug)
        except TelegraphChannel.DoesNotExist:
            return JsonResponse({"error": "Channel not found."}, status=404)

        file = request.FILES.get("image")
        if not file:
            return JsonResponse({"error": "No image file provided."}, status=400)

        content_type = file.content_type or ""
        if content_type not in _ALLOWED_IMAGE_TYPES:
            return JsonResponse({"error": "Unsupported file type. Send a JPEG, PNG, GIF, or WebP."}, status=415)

        if file.size > _MAX_MEDIA_BYTES:
            return JsonResponse({"error": "Image too large. Maximum size is 10 MB."}, status=413)

        from toto.people.models import Person
        from toto.telegraph.models import TelegraphMember

        person = Person.objects.filter(user=request.user).first()
        member = TelegraphMember.objects.filter(
            channel=channel, person=person, is_active=True
        ).select_related("person").first() if person else None

        image_bytes = file.read()
        image_data = f"data:{content_type};base64,{base64.b64encode(image_bytes).decode()}"

        display_name = (
            member.display_name if member
            else person.full_name if person
            else request.user.username
        )
        avatar_url = (
            _absolute_url(request, member.avatar_url) if member
            else None
        )

        payload = {
            "type": "image_message",
            "image_data": image_data,
            "user": display_name,
            "avatar_url": avatar_url,
        }

        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f"telegraph_{channel.slug}",
                {
                    "type": "chat_message",
                    "payload": payload,
                    "sender_channel": None,
                    "target_channel": None,
                },
            )

        return JsonResponse({"ok": True})


@method_decorator(csrf_exempt, name="dispatch")
class AudioUploadApiView(CorsApiView):
    def post(self, request, slug):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        try:
            channel = TelegraphChannel.objects.get(slug=slug)
        except TelegraphChannel.DoesNotExist:
            return JsonResponse({"error": "Channel not found."}, status=404)

        file = request.FILES.get("audio")
        if not file:
            return JsonResponse({"error": "No audio file provided."}, status=400)

        content_type = file.content_type or ""
        if not any(content_type.startswith(p) for p in _ALLOWED_AUDIO_PREFIXES):
            return JsonResponse(
                {"error": "Unsupported file type. Send audio/webm, audio/ogg, audio/mp4, audio/wav, or audio/mpeg."},
                status=415,
            )

        if file.size > _MAX_MEDIA_BYTES:
            return JsonResponse({"error": "Audio too large. Maximum size is 10 MB."}, status=413)

        from toto.people.models import Person
        from toto.telegraph.models import TelegraphMember

        person = Person.objects.filter(user=request.user).first()
        member = (
            TelegraphMember.objects.filter(channel=channel, person=person, is_active=True)
            .select_related("person")
            .first()
            if person
            else None
        )

        audio_bytes = file.read()
        audio_data = f"data:{content_type};base64,{base64.b64encode(audio_bytes).decode()}"

        display_name = (
            member.display_name if member
            else person.full_name if person
            else request.user.username
        )
        avatar_url = _absolute_url(request, member.avatar_url) if member else None

        payload = {
            "type": "voice_message",
            "audio_data": audio_data,
            "user": display_name,
            "avatar_url": avatar_url,
        }

        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f"telegraph_{channel.slug}",
                {
                    "type": "chat_message",
                    "payload": payload,
                    "sender_channel": None,
                    "target_channel": None,
                },
            )

        return JsonResponse({"ok": True})
