import secrets
from urllib.parse import urlencode

import requests as http_requests
from django.apps import apps
from django.contrib.auth import get_user_model, login, logout
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse

User = get_user_model()

_COOKIE_SALT = "oidc"
_COOKIE_MAX_AGE = 300  # 5 minutes — enough to survive the portal round-trip


def _cfg():
    return apps.get_app_config("sso_client").get_config()


def oidc_logout(request):
    logout(request)
    next_url = request.GET.get("next", "") or reverse("core:welcome")
    return redirect(next_url)


def oidc_login(request):
    cfg = _cfg()

    if not cfg.get("portal_url") or not cfg.get("client_id"):
        # No OIDC config in DB — fall back to the local username/password login.
        from django.urls import reverse as _reverse
        next_url = request.GET.get("next", "")
        fallback = _reverse("core:login")
        if next_url:
            fallback += f"?next={next_url}"
        return redirect(fallback)

    state = secrets.token_urlsafe(32)
    next_url = request.GET.get("next", "")

    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": _callback_uri(request),
        "scope": cfg["scopes"],
        "state": state,
    }
    response = redirect(f"{cfg['portal_url'].rstrip('/')}/sso/authorize/?{urlencode(params)}")
    # Store state in a signed cookie — survives the browser round-trip to the
    # portal without depending on the session being saved before the redirect.
    response.set_signed_cookie("oidc_state", state, salt=_COOKIE_SALT,
                               max_age=_COOKIE_MAX_AGE, httponly=True, samesite="Lax")
    if next_url:
        response.set_cookie("oidc_next", next_url, max_age=_COOKIE_MAX_AGE,
                            httponly=True, samesite="Lax")
    return response


def oidc_callback(request):
    cfg = _cfg()

    error = request.GET.get("error")
    if error:
        return HttpResponseBadRequest(f"Portal SSO error: {error}")

    state = request.GET.get("state")
    try:
        stored_state = request.get_signed_cookie("oidc_state", salt=_COOKIE_SALT,
                                                 max_age=_COOKIE_MAX_AGE)
    except Exception:
        stored_state = None

    if not state or state != stored_state:
        return HttpResponseBadRequest("Invalid OIDC state. Please try logging in again.")

    code = request.GET.get("code")
    if not code:
        return HttpResponseBadRequest("Missing authorization code.")

    portal = cfg["portal_url"].rstrip("/")

    token_resp = http_requests.post(
        f"{portal}/sso/token/",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _callback_uri(request),
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        },
        timeout=10,
    )
    if not token_resp.ok:
        return HttpResponseBadRequest(f"Token exchange failed: {token_resp.text}")

    access_token = token_resp.json().get("access_token")

    userinfo_resp = http_requests.get(
        f"{portal}/sso/userinfo/",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if not userinfo_resp.ok:
        return HttpResponseBadRequest("Userinfo fetch failed.")

    claims = userinfo_resp.json()
    user = _get_or_sync_user(claims)
    _link_person(user, claims.get("person_slug"))

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    next_url = request.COOKIES.get("oidc_next", "") or reverse("core:dashboard")
    response = redirect(next_url)
    response.delete_cookie("oidc_state")
    response.delete_cookie("oidc_next")
    return response


def _callback_uri(request):
    cfg = _cfg()
    for uri in cfg.get("redirect_uris", []):
        if uri.startswith("http://") or uri.startswith("https://"):
            return uri
    return request.build_absolute_uri(reverse("sso:callback"))


def _get_or_sync_user(claims):
    user = _find_existing_user_for_claims(claims)
    if user is None:
        username = f"oidc_{claims['sub']}"
        user, _ = User.objects.get_or_create(username=username)

    changed = False
    for field, key in [("email", "email"), ("first_name", "given_name"), ("last_name", "family_name")]:
        val = claims.get(key, "")
        if getattr(user, field) != val:
            setattr(user, field, val)
            changed = True
    if changed:
        user.save(update_fields=["email", "first_name", "last_name"])
    return user


def _find_existing_user_for_claims(claims):
    preferred_username = claims.get("preferred_username", "").strip()
    if preferred_username:
        user = User.objects.filter(username=preferred_username).first()
        if user:
            return user

    email = claims.get("email", "").strip()
    if email:
        return User.objects.filter(email__iexact=email).first()

    return None


def _link_person(user, person_slug):
    if not person_slug:
        return
    try:
        from toto.people.models import Person
        person = Person.objects.filter(slug=person_slug, user__isnull=True).first()
        if person:
            person.user = user
            person.save(update_fields=["user"])
    except Exception:
        pass
