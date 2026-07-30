import logging
import secrets
from urllib.parse import urlencode

import requests as http_requests
from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse

logger = logging.getLogger(__name__)

User = get_user_model()

_COOKIE_SALT = "oidc"
_COOKIE_MAX_AGE = 300  # 5 minutes — enough to survive the portal round-trip

# Roles the provider grants (toto.sso_master.services.get_user_claims): a
# superuser is ["admin", "staff"], staff is ["staff"], everyone else ["viewer"].
ROLE_ADMIN = "admin"
ROLE_STAFF = "staff"


def _cfg():
    return apps.get_app_config("sso_client").get_config()


# (connect, read) rather than one number. A slow provider is worse than a dead
# one: this host's worker is held for the whole wait, and with the four workers
# deploy.py hardcodes, four concurrent logins against a stalled provider take the
# consumer down with it. Connect fails fast; read allows for the provider
# re-deriving its signing key on the token endpoint.
_BACKCHANNEL_TIMEOUT = (2, 5)


def _tls_verify():
    """What to pass as ``requests``' ``verify=``.

    True (the system CA bundle) unless the host names a bundle. Needed because a
    provider on a self-signed certificate — every local federated pair, and any
    deployment fronted by an internal CA — otherwise raises SSLError on the token
    exchange, which is why the shipped local federated preset could never have
    worked. Set SSO_CLIENT_CA_BUNDLE to a PEM path, or SSO_CLIENT_VERIFY=False to
    turn verification off (development only; it makes the back-channel forgeable).
    """
    bundle = getattr(settings, "SSO_CLIENT_CA_BUNDLE", "")
    if bundle:
        return bundle
    return bool(getattr(settings, "SSO_CLIENT_VERIFY", True))


def _provider_unreachable(request, why: str):
    """Fail a federated sign-in without failing the whole login page.

    Sends the user to the local password form, because on a host that has any
    local accounts at all that form still works while the provider is down — the
    federated round trip is one way in, not the only one. Falls back to a plain
    400 where there is no local form to offer.
    """
    try:
        target = reverse("core:login")
    except NoReverseMatch:
        return HttpResponseBadRequest(f"Federated sign-in failed: {why}.")
    messages.error(request, f"Federated sign-in failed: {why}. You can sign in here instead.")
    return redirect(target)


def auto_provision_enabled():
    """May an account unknown to this host be created on first sign-in?

    Off by default, and deliberately so: with it on, every account on the
    provider silently becomes an account here the moment someone follows a link.
    That is right for a public consumer and wrong for an internal one, so the
    host decides. Mirrors toto.social_login's TOTO_SOCIAL_SIGNUP.

    Read as a settings value, not through toto.features.flag: the host has
    already resolved its environment by the time settings are loaded, so this
    arrives as a real bool. (flag() only accepts the literal string "1", which a
    settings module's `True` would fail.)
    """
    return bool(getattr(settings, "TOTO_SSO_AUTO_PROVISION", False))


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

    try:
        token_resp = http_requests.post(
            f"{portal}/sso/token/",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _callback_uri(request),
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
            },
            timeout=_BACKCHANNEL_TIMEOUT,
            verify=_tls_verify(),
        )
    except http_requests.RequestException as exc:
        # Unreachable, refused, timed out, or a TLS failure. Uncaught this was a
        # 500 with a traceback and no clue that the *provider* was the problem.
        logger.warning("SSO token exchange to %s failed: %s", portal, exc)
        return _provider_unreachable(request, "could not reach the sign-in provider")

    if not token_resp.ok:
        return HttpResponseBadRequest(f"Token exchange failed: {token_resp.text}")

    try:
        access_token = token_resp.json().get("access_token")
    except ValueError:
        # A 200 that is not JSON — a captive portal or a proxy error page.
        logger.warning("SSO token endpoint at %s returned non-JSON", portal)
        return _provider_unreachable(request, "the sign-in provider sent an unreadable reply")

    try:
        userinfo_resp = http_requests.get(
            f"{portal}/sso/userinfo/",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_BACKCHANNEL_TIMEOUT,
            verify=_tls_verify(),
        )
    except http_requests.RequestException as exc:
        logger.warning("SSO userinfo fetch from %s failed: %s", portal, exc)
        return _provider_unreachable(request, "could not reach the sign-in provider")

    if not userinfo_resp.ok:
        return HttpResponseBadRequest("Userinfo fetch failed.")

    try:
        claims = userinfo_resp.json()
    except ValueError:
        logger.warning("SSO userinfo endpoint at %s returned non-JSON", portal)
        return _provider_unreachable(request, "the sign-in provider sent an unreadable reply")
    user = _get_or_sync_user(claims)
    if user is None:
        return HttpResponseBadRequest(
            "No account on this platform for that sign-in, and self-provisioning "
            "is disabled. Ask an administrator for access."
        )
    if not user.is_active:
        # The provider is the authority on whether an account is live, and this
        # is the only channel that carries a deactivation — a disabled user can
        # never reach /authorize, so without this check their existing local
        # account would keep working here forever.
        return HttpResponseBadRequest("That account is disabled.")

    _link_person(user, claims.get("person_slug"), claims.get("sub"))

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
    """The local account for these claims, or None if we may not create one.

    Every field here is owned by the provider and refreshed on each sign-in,
    including the privilege flags — so revoking staff there revokes it here on
    the user's next login rather than leaving a stale local grant behind.
    """
    user = _find_existing_user_for_claims(claims)
    if user is None:
        if not auto_provision_enabled():
            return None
        username = f"oidc_{claims['sub']}"
        user, created = User.objects.get_or_create(username=username)
        if created:
            # get_or_create leaves password="", which has_usable_password()
            # reports as USABLE — so the account would look password-backed to
            # the reset flow and to any "set a password" affordance, despite no
            # password being able to authenticate it. Say what is true: this
            # account is reachable only through the provider.
            user.set_unusable_password()
            user.save(update_fields=["password"])

    updates = {
        "email": claims.get("email", ""),
        "first_name": claims.get("given_name", ""),
        "last_name": claims.get("family_name", ""),
    }
    # The roles scope is optional: a provider that does not grant it leaves the
    # claim absent, and local flags are then left exactly as they are rather
    # than being silently cleared.
    if "roles" in claims:
        roles = set(claims.get("roles") or [])
        updates["is_superuser"] = ROLE_ADMIN in roles
        updates["is_staff"] = bool(roles & {ROLE_ADMIN, ROLE_STAFF})
    if "is_active" in claims:
        updates["is_active"] = bool(claims["is_active"])

    changed = [f for f, v in updates.items() if getattr(user, f) != v]
    if changed:
        for field in changed:
            setattr(user, field, updates[field])
        user.save(update_fields=changed)
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


def _link_person(user, person_slug, subject=None):
    """Give the signed-in user a community profile on this host.

    Three cases, in order:

    1. A Person already carries this provider subject — the returning federated
       user. Keep it attached to the (possibly renamed) account.
    2. A Person exists with the provider's slug and no user — a record seeded
       locally that we now recognise. Adopt it.
    3. Nothing matches — create one.

    Case 3 is why this exists. Several apps a consumer host is likely to install
    have NOT NULL foreign keys to Person (kanban.Project.project_lead,
    kanban.Practitioner.person, forum.ForumMember.person), and a consumer host
    starts with an empty people table. Adoption alone never fires there, so
    without this the host comes up unable to create a project or join a channel.

    Best-effort by design: a host may not install toto.people at all, and a
    profile is not worth failing a login over.
    """
    try:
        from toto.people.models import Person
    except Exception:
        return

    try:
        if subject:
            person = Person.objects.filter(federated_sub=subject).first()
            if person:
                if person.user_id != user.pk:
                    person.user = user
                    person.save(update_fields=["user"])
                return person

        if person_slug:
            person = Person.objects.filter(slug=person_slug, user__isnull=True).first()
            if person:
                person.user = user
                person.federated_sub = subject or ""
                person.save(update_fields=["user", "federated_sub"])
                return person

        if Person.objects.filter(user=user).exists():
            return None

        return Person.objects.create(
            user=user,
            display_name=(user.get_full_name() or user.get_username()),
            email=user.email or "",
            federated_sub=subject or "",
        )
    except Exception:
        return None
