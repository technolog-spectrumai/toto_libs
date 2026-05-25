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

CORS_ALLOW_HEADERS = "Content-Type, X-Requested-With, Authorization"
CORS_ALLOW_METHODS = "GET, POST, OPTIONS"


def _is_allowed_origin(origin: str) -> bool:
    """Allow localhost / 127.0.0.1 (any port) and Tauri scheme origins."""
    if not origin:
        return False
    try:
        parsed = urlparse(origin)
        hostname = parsed.hostname or ""
        return hostname in ("localhost", "127.0.0.1") or parsed.scheme == "tauri"
    except Exception:
        return False


def _cors(request, response):
    origin = request.META.get("HTTP_ORIGIN", "")
    if _is_allowed_origin(origin):
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Credentials"] = "true"
    response["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
    response["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
    return response


def _try_bearer_auth(request):
    """Authenticate request via Authorization: Bearer <session_key> header."""
    if request.user.is_authenticated:
        return
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return
    session_key = auth_header[7:].strip()
    if not session_key:
        return
    try:
        store = SessionStore(session_key=session_key)
        user_id = store.get("_auth_user_id")
        if not user_id:
            return
        User = get_user_model()
        request.user = User.objects.get(pk=user_id)
    except Exception:
        pass


class CorsApiView(View):
    """Base view: handles CORS preflight, injects CORS headers, and accepts Bearer auth."""

    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS":
            return _cors(request, HttpResponse())
        _try_bearer_auth(request)
        response = super().dispatch(request, *args, **kwargs)
        return _cors(request, response)


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


def _member_to_dict(request, member):
    return {
        "name": member.display_name,
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
        }

        try:
            from toto.people.models import Person
            person = Person.objects.filter(user=user).first()
            if person:
                data["full_name"] = person.full_name or data["full_name"]
                data["avatar_url"] = request.build_absolute_uri(person.avatar.url) if person.avatar else None
                data["profile_url"] = f"/socialhub/profiles/{person.slug}/"
        except Exception:
            pass

        return JsonResponse(data)


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

        members_qs = channel.telegraph_members.filter(is_active=True).select_related("person")
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
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


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

        if file.size > _MAX_IMAGE_BYTES:
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
