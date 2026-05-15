import json
from urllib.parse import urlparse

from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import models
from toto.enigma.models import Room

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
        return JsonResponse({"ok": True, "service": "enigma"})


def _room_to_dict(room, participant_count=None):
    return {
        "id": room.id,
        "name": room.name,
        "slug": room.slug,
        "participant_count": participant_count if participant_count is not None else 0,
        "created_at": room.created_at.isoformat(),
    }


def _absolute_url(request, url):
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return url
    return request.build_absolute_uri(url)


def _participant_to_dict(request, participant):
    return {
        "name": participant.display_name,
        "avatar_url": _absolute_url(request, participant.avatar_url),
        "type": participant.participant_type,
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
            from toto.socialhub.models import Person
            person = Person.objects.filter(user=user).first()
            if person:
                data["full_name"] = person.full_name or data["full_name"]
                data["avatar_url"] = request.build_absolute_uri(person.avatar.url) if person.avatar else None
                data["profile_url"] = f"/socialhub/profiles/{person.slug}/"
        except Exception:
            pass

        return JsonResponse(data)


@method_decorator(csrf_exempt, name="dispatch")
class RoomListApiView(CorsApiView):
    def get(self, request):
        qs = Room.objects.annotate(
            participant_count=models.Count(
                "chat_participants",
                filter=models.Q(chat_participants__is_active=True),
                distinct=True,
            )
        ).order_by("name")
        return JsonResponse({"rooms": [_room_to_dict(r, r.participant_count) for r in qs]})


@method_decorator(csrf_exempt, name="dispatch")
class RoomDetailApiView(CorsApiView):
    def get(self, request, slug):
        try:
            room = Room.objects.get(slug=slug)
        except Room.DoesNotExist:
            return JsonResponse({"error": "Room not found."}, status=404)

        participants_qs = room.chat_participants.filter(is_active=True).select_related("person")
        participants = [
            _participant_to_dict(request, p)
            for p in participants_qs
        ]

        is_participant = False
        if request.user and request.user.is_authenticated:
            try:
                from toto.socialhub.models import Person
                person = Person.objects.filter(user=request.user).first()
                if person:
                    is_participant = room.chat_participants.filter(person=person, is_active=True).exists()
            except Exception:
                pass

        data = _room_to_dict(room, participants_qs.count())
        data["participants"] = participants
        data["is_participant"] = is_participant
        return JsonResponse(data)


@method_decorator(csrf_exempt, name="dispatch")
class RoomJoinApiView(CorsApiView):
    def post(self, request, slug):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            room = Room.objects.get(slug=slug)
        except Room.DoesNotExist:
            return JsonResponse({"error": "Room not found."}, status=404)

        from toto.socialhub.models import Person
        from toto.enigma.models import Participant

        person = Person.objects.filter(user=request.user).first()
        if not person:
            return JsonResponse({"error": "No person profile linked to this account."}, status=403)

        participant, created = Participant.objects.get_or_create(
            room=room, person=person, defaults={"is_active": True}
        )
        if not participant.is_active:
            participant.is_active = True
            participant.save(update_fields=["is_active"])

        room.participants.add(request.user)
        return JsonResponse({"ok": True, "joined": created})


@method_decorator(csrf_exempt, name="dispatch")
class RoomLeaveApiView(CorsApiView):
    def post(self, request, slug):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            room = Room.objects.get(slug=slug)
        except Room.DoesNotExist:
            return JsonResponse({"error": "Room not found."}, status=404)

        from toto.socialhub.models import Person
        from toto.enigma.models import Participant

        person = Person.objects.filter(user=request.user).first()
        room.participants.remove(request.user)
        if person:
            Participant.objects.filter(room=room, person=person, is_active=True).update(is_active=False)

        return JsonResponse({"ok": True})


@method_decorator(csrf_exempt, name="dispatch")
class RoomLeaveAllApiView(CorsApiView):
    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        from toto.socialhub.models import Person
        from toto.enigma.models import Participant

        person = Person.objects.filter(user=request.user).first()
        if not person:
            return JsonResponse({"ok": True, "left": 0})

        active_rooms = Room.objects.filter(
            chat_participants__person=person,
            chat_participants__is_active=True,
        ).distinct()
        left = active_rooms.count()

        for room in active_rooms:
            room.participants.remove(request.user)

        Participant.objects.filter(person=person, is_active=True).update(is_active=False)

        return JsonResponse({"ok": True, "left": left})
