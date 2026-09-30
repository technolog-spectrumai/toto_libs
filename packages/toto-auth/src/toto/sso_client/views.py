import logging
import secrets
from urllib.parse import urlencode

import requests as http_requests
from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from toto.core.safe_next import safe_next

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


def _endpoint(cfg, key, portal, fallback_path):
    """A provider endpoint: learned at pairing, or built the old way.

    A paired connection carries the provider's own endpoints, so a provider
    mounted at anything other than the default prefix works. A row that predates
    pairing has them blank and falls back to string concatenation, which is what
    this always did.
    """
    return (cfg.get(key) or "").strip() or f"{portal}{fallback_path}"


def oidc_logout(request):
    logout(request)
    # Only a place on this site (2026-09-30), like the password doors.
    return redirect(safe_next(request, request.GET.get("next"), reverse("core:welcome")))


def _federation_configured(cfg) -> bool:
    return bool(cfg.get("portal_url") and cfg.get("client_id"))


def local_login_enabled() -> bool:
    """Does this consumer have accounts of its own?

    Off by default, so a pure consumer host is unchanged: ``sso:login`` keeps
    redirecting straight to the provider. On, ``sso:login`` becomes a page
    offering both, which is what a host with studio-only users needs.
    """
    return bool(getattr(settings, "TOTO_SSO_LOCAL_LOGIN", False))


def oidc_login(request):
    """The consumer's ``sso:login`` — a page, a redirect, or a fallback.

    ``LOGIN_URL`` is ``"sso:login"`` in every auth mode (``auth_config``), so this
    is where ``@login_required`` sends everyone. It used to redirect to the
    provider unconditionally, which meant a host with local accounts bounced its
    own users to a portal that has never heard of them and answered 400. With
    ``TOTO_SSO_LOCAL_LOGIN`` on it renders the shared password form instead, with
    federated sign-in as a button — so both kinds of user have a way in and
    neither is special-cased in the urlconf.

    Deliberately NOT a fourth auth mode: ``login_url()`` and
    ``authentication_backends()`` stay identical across the three modes, which
    three tests in test_auth_config.py assert, and ModelBackend already served
    every mode.
    """
    cfg = _cfg()
    next_url = safe_next(request, request.GET.get("next"))

    if not _federation_configured(cfg):
        # No OIDC config in DB — fall back to the local username/password login.
        from django.urls import reverse as _reverse
        fallback = _reverse("core:login")
        if next_url:
            fallback += f"?next={next_url}"
        return redirect(fallback)

    if local_login_enabled():
        from toto.core.auth_views import password_login_view

        federated_url = reverse("sso:federated_login")
        if next_url:
            federated_url += f"?next={next_url}"
        return password_login_view(
            request,
            template_name="sso_client/login.html",
            page_title="Sign in",
            extra_context={
                "federated_url": federated_url,
                "federated_label": cfg.get("label") or "the portal",
                # The form must post back HERE, not to core:login — this view is
                # what knows about the federated button.
                "login_form_action": reverse("sso:login"),
            },
        )

    return federated_login(request)


def federated_login(request, *, linking=False):
    """Start the OIDC round trip. Reached directly, or from the hybrid page.

    ``linking`` means "attach the identity that comes back to the already
    signed-in user" rather than "sign somebody in". Same round trip either way, so
    there is one implementation of state, PKCE-less redirect and cookie handling
    to get right rather than two.
    """
    cfg = _cfg()

    if not _federation_configured(cfg):
        return redirect(reverse("core:login"))

    state = secrets.token_urlsafe(32)
    # Checked on the way in and again on the way back (2026-09-30): the
    # cookie outlives this request, and the callback follows it after signing
    # the member in.
    next_url = safe_next(request, request.GET.get("next"))

    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": _callback_uri(request),
        "scope": cfg["scopes"],
        "state": state,
    }
    authorize = _endpoint(
        cfg, "authorization_endpoint", cfg["portal_url"].rstrip("/"), "/sso/authorize/",
    )
    response = redirect(f"{authorize}?{urlencode(params)}")
    # Store state in a signed cookie — survives the browser round-trip to the
    # portal without depending on the session being saved before the redirect.
    response.set_signed_cookie("oidc_state", state, salt=_COOKIE_SALT,
                               max_age=_COOKIE_MAX_AGE, httponly=True, samesite="Lax")
    if next_url:
        response.set_cookie("oidc_next", next_url, max_age=_COOKIE_MAX_AGE,
                            httponly=True, samesite="Lax")
    if linking:
        # Signed, so the callback cannot be talked into linking by a crafted
        # request; short-lived like the state it travels with.
        response.set_signed_cookie("oidc_link", "1", salt=_COOKIE_SALT,
                                   max_age=_COOKIE_MAX_AGE, httponly=True,
                                   samesite="Lax")
    return response


@login_required
def federated_link(request):
    """Attach a federated identity to the account already signed in here.

    This is the deliberate act that replaces matching on email. Only the
    account's own authenticated session can start it, which is what makes it safe:
    a provider cannot claim a local account, and a local user cannot claim someone
    else's provider identity.

    It is also the answer for a host whose users authenticate by password —
    linking never calls ``set_unusable_password()``, so an account that a shipped
    desktop binary signs into keeps working.
    """
    return federated_login(request, linking=True)


def _complete_link(request, claims):
    """Finish a linking round trip. Returns a response; never signs anybody in."""
    from .models import FederatedIdentity

    provider = _active_provider()
    if provider is None:
        messages.error(request, _("This host has no active sign-in provider configured."))
        return redirect(reverse("core:dashboard"))

    sub = (claims.get("sub") or "").strip()
    if not sub:
        messages.error(request, _("The provider did not identify the account."))
        return redirect(reverse("core:dashboard"))

    existing = FederatedIdentity.objects.filter(provider=provider, sub=sub).first()
    if existing is not None and existing.user_id != request.user.pk:
        # Someone else already answers to this subject. Refused rather than
        # reassigned: silently moving it would hand this user the other account's
        # federated route in.
        logger.warning(
            "Refused to link provider subject already held by user %s", existing.user_id,
        )
        messages.error(
            request,
            _("That provider account is already linked to a different account here."),
        )
        return redirect(reverse("core:dashboard"))

    if existing is None:
        FederatedIdentity.objects.create(
            provider=provider, sub=sub, user=request.user, provisioned=False,
        )
        messages.success(
            request,
            _("You can now sign in with %(provider)s as well as with your password.")
            % {"provider": provider.label},
        )
    else:
        messages.info(
            request,
            _("%(provider)s was already linked to this account.")
            % {"provider": provider.label},
        )

    next_url = safe_next(request, request.COOKIES.get("oidc_next"), reverse("core:dashboard"))
    response = redirect(next_url)
    response.delete_cookie("oidc_state")
    response.delete_cookie("oidc_next")
    response.delete_cookie("oidc_link")
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
            _endpoint(cfg, "token_endpoint", portal, "/sso/token/"),
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
            _endpoint(cfg, "userinfo_endpoint", portal, "/sso/userinfo/"),
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
    # A linking round trip attaches the identity to the session that started it
    # and never touches account fields — no claim from the provider may rename,
    # re-email or re-privilege an account a local user already owns.
    if _is_linking(request) and request.user.is_authenticated:
        return _complete_link(request, claims)

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

    next_url = safe_next(request, request.COOKIES.get("oidc_next"), reverse("core:dashboard"))
    response = redirect(next_url)
    response.delete_cookie("oidc_state")
    response.delete_cookie("oidc_next")
    return response


def _is_linking(request) -> bool:
    """Was this round trip started by federated_link rather than a sign-in?"""
    try:
        return request.get_signed_cookie(
            "oidc_link", salt=_COOKIE_SALT, max_age=_COOKIE_MAX_AGE,
        ) == "1"
    except Exception:
        return False


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
    identity = _find_identity_for_claims(claims)
    user = identity.user if identity is not None else None
    if user is None:
        if not auto_provision_enabled():
            return None
        username = _provisioned_username(claims)
        user, created = User.objects.get_or_create(username=username)
        if created:
            # get_or_create leaves password="", which has_usable_password()
            # reports as USABLE — so the account would look password-backed to
            # the reset flow and to any "set a password" affordance, despite no
            # password being able to authenticate it. Say what is true: this
            # account is reachable only through the provider.
            user.set_unusable_password()
            user.save(update_fields=["password"])
        identity = _record_identity(claims, user, provisioned=True)

    if identity is not None:
        identity.last_login_at = timezone.now()
        identity.save(update_fields=["last_login_at"])

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


def _active_provider():
    """The OIDCProviderConfig row backing the current config, or None."""
    from .models import OIDCProviderConfig

    return OIDCProviderConfig.objects.filter(active=True).order_by("-imported_at").first()


def _find_identity_for_claims(claims):
    """The recorded identity for these claims, or None. Never a guess.

    Matching used to fall back to ``username`` and then ``email__iexact``, with
    nothing to distinguish a local-only account from a federated one — so a local
    user whose email matched a provider account was silently absorbed by it and
    could be handed ``is_staff``/``is_superuser`` by the next claim. See
    ``FederatedIdentity``. Only an identity this host has been told about matches
    now; anything else is either a provisioning decision or a refusal.
    """
    from .models import FederatedIdentity

    sub = (claims.get("sub") or "").strip()
    if not sub:
        return None
    provider = _active_provider()
    if provider is None:
        return None
    return (
        FederatedIdentity.objects
        .select_related("user")
        .filter(provider=provider, sub=sub)
        .first()
    )


def _provisioned_username(claims):
    """The username for a freshly provisioned federated account.

    ``oidc_<sub>`` — deliberately not the provider's ``preferred_username``, which
    can collide with a local account and is mutable there. Where a human-readable
    name is wanted, link the accounts instead of guessing they are the same.
    """
    return f"oidc_{claims['sub']}"


def _record_identity(claims, user, *, provisioned):
    """Write down that this account answers to this provider subject."""
    from .models import FederatedIdentity

    provider = _active_provider()
    if provider is None:
        # Nothing to key the identity on. The sign-in still completes; it simply
        # will not be remembered, which fails closed on the next attempt rather
        # than matching something by coincidence.
        logger.warning("No active OIDCProviderConfig; not recording a federated identity")
        return None
    identity, _created = FederatedIdentity.objects.get_or_create(
        provider=provider, sub=claims["sub"],
        defaults={"user": user, "provisioned": provisioned},
    )
    return identity


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


# ---------------------------------------------------------------------------
# The child's federation console — the consumer mirror of sso_master's.
# ---------------------------------------------------------------------------


def _redeem_code(request, context):
    """Redeem a pairing code from the console form. Returns the config on success, else
    ``None`` with ``context["error"]`` set (and the form values preserved for re-render).

    The single-page form of the admin's two-step ``federate_view``: canonicalise the
    named platform, refuse plaintext, accept a pasted code or an uploaded QR image, then
    ``pairing.pair`` — which checks the code names the platform we asked for BEFORE it
    transmits anything.
    """
    from toto.sso_core import qr

    from .pairing import PairingError, pair, platform_url

    raw_target = (request.POST.get("target") or "").strip()
    context["target"] = raw_target
    context["code"] = (request.POST.get("code") or "").strip()
    try:
        target = platform_url(raw_target)
    except PairingError as exc:
        context["error"] = exc.message
        return None
    if target.startswith("http://") and not settings.DEBUG:
        context["error"] = (
            f"{target} is not secure — a platform will not accept a pairing code over "
            "plain HTTP. Use https://."
        )
        return None
    context["target"] = target

    code = context["code"]
    upload = request.FILES.get("image")
    if upload:
        try:
            code = qr.read(upload.read())
            context["code"] = code
        except qr.QRError as exc:
            context["error"] = str(exc)
            return None
    if not code:
        context["error"] = "Paste the pairing code, or upload a picture of it."
        return None

    try:
        return pair(
            code,
            callback_uri=context["callback_uri"],
            label=(request.POST.get("label") or ""),
            expect_url=target,
        )
    except PairingError as exc:
        context["error"] = exc.message
        return None


@login_required
def federation_console(request):
    """Who this host is federated with, and how to (re)pair — the child's console.

    Staff-only, and 403 (via ``PermissionDenied``) for a signed-in non-staff user rather
    than a redirect, matching the provider's console and the jess pages. When paired it
    shows the parent's live identity (name/domain/logo) from the platform-info API — which
    also proves the secret works — and hides the code box behind an explicit control. When
    not paired it shows the redeem form directly.
    """
    from django.core.exceptions import PermissionDenied
    from django.shortcuts import render

    from toto.ui import PageProcessor

    from . import parent_info
    from .models import OIDCProviderConfig

    if not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied

    config = OIDCProviderConfig.objects.filter(active=True).first()
    cfg = apps.get_app_config("sso_client").get_config()
    paired = bool(config and config.paired_at and cfg.get("portal_url") and cfg.get("client_id"))

    try:
        callback_uri = request.build_absolute_uri(reverse("sso:callback"))
    except NoReverseMatch:
        callback_uri = ""

    context = {
        "page_title": "Federation",
        "config": config,
        "paired": paired,
        "parent": None,
        "parent_unreachable": False,
        "error": None,
        "code": "",
        "target": (request.GET.get("target") or (config.portal_url if config else "") or "").strip(),
        "callback_uri": callback_uri,
    }

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "test":
            info = parent_info.fetch_platform_info(cfg)
            if info:
                messages.success(
                    request,
                    _("Connection OK — reached %s.")
                    % (info.get("site_name") or cfg.get("portal_url")),
                )
            else:
                messages.error(
                    request,
                    _("Could not reach the parent with the stored credentials. Check it is "
                      "running, or re-pair."),
                )
            return redirect(reverse("sso:federation_console"))
        if action == "redeem":
            result = _redeem_code(request, context)
            if result is not None:
                messages.success(request, _("Federated with %s.") % result.label)
                return redirect(reverse("sso:federation_console"))
            # else: fall through and re-render with context["error"] + the typed values

    if paired and context["error"] is None:
        info = parent_info.fetch_platform_info(cfg)
        if info:
            context["parent"] = info
        else:
            context["parent_unreachable"] = True

    return render(
        request, "sso_client/federation_console.html",
        PageProcessor().decorate(context, request),
    )
