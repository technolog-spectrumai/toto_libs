import secrets
from urllib.parse import urlencode

import requests as http_requests
from django.apps import apps
from django.contrib.auth import get_user_model, login, logout
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse

User = get_user_model()


def _cfg():
    return apps.get_app_config("sso_client").get_config()


def oidc_logout(request):
    cfg = _cfg()
    next_url = request.GET.get("next", "")
    logout(request)
    # Optionally redirect to the portal's logout so the portal session is also cleared
    portal_logout = f"{cfg['portal_url'].rstrip('/')}/sso/logout/"
    target = next_url or portal_logout
    return redirect(target)


def oidc_login(request):
    cfg = _cfg()
    state = secrets.token_urlsafe(32)
    request.session["oidc_state"] = state

    next_url = request.GET.get("next", "")
    if next_url:
        request.session["oidc_next"] = next_url

    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": _callback_uri(request),
        "scope": cfg["scopes"],
        "state": state,
    }
    return redirect(f"{cfg['portal_url'].rstrip('/')}/sso/authorize/?{urlencode(params)}")


def oidc_callback(request):
    cfg = _cfg()

    error = request.GET.get("error")
    if error:
        return HttpResponseBadRequest(f"Portal SSO error: {error}")

    state = request.GET.get("state")
    if not state or state != request.session.pop("oidc_state", None):
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

    next_url = request.session.pop("oidc_next", "") or reverse("core:dashboard")
    return redirect(next_url)


def _callback_uri(request):
    return request.build_absolute_uri(reverse("sso:callback"))


def _get_or_sync_user(claims):
    username = f"oidc_{claims['sub']}"
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "email": claims.get("email", ""),
            "first_name": claims.get("given_name", ""),
            "last_name": claims.get("family_name", ""),
        },
    )
    if not created:
        changed = False
        for field, key in [("email", "email"), ("first_name", "given_name"), ("last_name", "family_name")]:
            val = claims.get(key, "")
            if getattr(user, field) != val:
                setattr(user, field, val)
                changed = True
        if changed:
            user.save(update_fields=["email", "first_name", "last_name"])
    return user


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
