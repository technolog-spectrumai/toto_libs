"""JSON auth API for programmatic SSO clients (e.g. the Enigma Cloud desktop app).

Registration lives here in the SSO master (the identity authority). ``toto.api``
exposes only the session *login*, which this mirrors. A
successful registration logs the new user in and returns the Django session key,
usable as ``Authorization: Bearer <token>`` exactly like the login endpoint.
"""

import json

from django.contrib.auth import authenticate, get_user_model, login
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from toto.api.cors import CorsApiView


@method_decorator(csrf_exempt, name="dispatch")
class RegisterApiView(CorsApiView):
    """`POST /sso/api/register/` — self-service signup (Enigma Cloud).

    Gated by ``settings.SSO_OPEN_REGISTRATION`` (library default: False; a
    personal server like faros opts in). On success the user is created AND
    logged in, returning the session key exactly like ``toto.api``'s LoginApiView.
    """

    def post(self, request):
        from django.conf import settings
        from django.core.exceptions import ValidationError
        from django.contrib.auth.password_validation import validate_password
        from django.db import IntegrityError, transaction

        if not getattr(settings, "SSO_OPEN_REGISTRATION", False):
            return JsonResponse({"error": "Registration is disabled."}, status=403)

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)
        if not isinstance(body, dict):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        username = str(body.get("username", "")).strip()
        password = body.get("password", "")
        if not isinstance(password, str):
            password = ""
        if not username or not password:
            return JsonResponse({"error": "Username and password required."}, status=400)

        User = get_user_model()
        # Validate the username against the model's field validators (charset via
        # UnicodeUsernameValidator, max length) so we reject spaces/control chars
        # and over-long names instead of storing junk or 500ing on the prod DB.
        candidate = User(username=username)
        try:
            # validate_unique=False: uniqueness is handled below (case-insensitive
            # check + IntegrityError guard); here we only want the charset/length
            # field validators, not a DB uniqueness probe that would mask a 409 as 400.
            candidate.full_clean(
                exclude=[f.name for f in User._meta.fields if f.name != "username"],
                validate_unique=False,
            )
        except ValidationError:
            return JsonResponse({"error": "Invalid username."}, status=400)

        # Enforce the project's configured password policy
        # (AUTH_PASSWORD_VALIDATORS), not just a bare length check — this
        # endpoint is internet/Tor-facing.
        try:
            validate_password(password, user=candidate)
        except ValidationError as exc:
            return JsonResponse({"error": " ".join(exc.messages)}, status=400)

        # Case-insensitive uniqueness check; the stored username keeps its case.
        # The DB constraint is case-sensitive, so guard the create with a
        # transaction and treat an IntegrityError (exact-case race) as 409.
        if User.objects.filter(username__iexact=username).exists():
            return JsonResponse({"error": "Username already taken."}, status=409)

        try:
            with transaction.atomic():
                User.objects.create_user(username=username, password=password)
        except IntegrityError:
            return JsonResponse({"error": "Username already taken."}, status=409)

        user = authenticate(request, username=username, password=password)
        if user is None:
            return JsonResponse({"error": "Invalid credentials."}, status=401)

        login(request, user)
        return JsonResponse({"ok": True, "token": request.session.session_key}, status=201)
