"""Auth, identity and capability endpoints.

These were historically defined in ``toto.telegraph.api_views`` and served under
``/telegraph/api/`` — an accident of history, not a design: none of them is chat. They
moved here during the telegraph → forum rework so the chat app owns only chat, and so
renaming it could not disturb the identity contract a shipped desktop client depends on.

They are mounted at ``/api/`` and, for that shipped client, also aliased under
``/telegraph/api/``. See ``toto/api/urls.py``.
"""
import json

from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .cors import DATA_MESH_GROUP, MESH_DOMAINS, CorsApiView, in_data_mesh


@method_decorator(csrf_exempt, name="dispatch")
class MeshMeApiView(CorsApiView):
    """`GET /api/me/mesh/` — whether the user can read gated data from the server
    (else the client must peer-pull)."""

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
        return JsonResponse({"ok": True, "service": "toto"})


# Feature key (what the edge client shows in its dashboard/nav) → the Django app that
# backs it. The descriptor reports which are installed on THIS server so a client (e.g.
# Enigma+) only offers apps the backend can actually serve — zenobia has them all, faros
# (the minimal Tor server) lacks e.g. the knowledge graph (ravioli).
_FEATURE_APPS = {
    "chat": "toto.forum",
    "vault": "toto.vault",
    "tasks": "toto.kanban",
    "locations": "toto.locations",
    "people": "toto.socialhub",
    "events": "toto.events",
    "graph": "toto.ravioli",
}


@method_decorator(csrf_exempt, name="dispatch")
class AppsApiView(CorsApiView):
    """`GET /api/apps/` — capability descriptor.

    Returns ``{"apps": {feature: bool}}`` for each known feature, based on whether its
    backing Django app is installed on this server. No auth required (capability info is
    not sensitive) and available on every server tier.
    """

    def get(self, request):
        from django.apps import apps as django_apps

        available = {
            key: django_apps.is_installed(label) for key, label in _FEATURE_APPS.items()
        }
        return JsonResponse({"apps": available})


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
