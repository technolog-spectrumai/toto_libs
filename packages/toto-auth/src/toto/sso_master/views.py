import base64
from urllib.parse import urlencode, urlparse

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from toto.core.auth_views import password_login_view, password_logout_view
from .models import SSOAccessToken, SSOAuthorizationCode, SSORelyingParty
from .services import (
    build_id_token,
    get_issuer,
    get_jwks,
    get_public_base_url,
    get_subject_for_user,
    get_user_claims,
    verify_pkce,
)


def login_view(request):
    return password_login_view(
        request, template_name="sso/login.html", page_title="Sign In"
    )


def logout_view(request):
    return password_logout_view(request)


def _openid_configuration_payload(request, authorization_endpoint: str) -> dict:
    return {
        "issuer": get_issuer(request),
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": request.build_absolute_uri(reverse("sso:token")),
        "userinfo_endpoint": request.build_absolute_uri(reverse("sso:userinfo")),
        "jwks_uri": request.build_absolute_uri(reverse("sso:jwks")),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "profile", "roles"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "claims_supported": [
            "iss", "sub", "aud", "exp", "iat", "auth_time", "nonce",
            "email", "email_verified", "name", "preferred_username",
            "given_name", "family_name", "display_name", "person_slug",
            "roles", "is_superuser",
        ],
        # S256 only: plain gives a public client no protection, because the
        # challenge travels in the /authorize URL. See services.verify_pkce.
        "code_challenge_methods_supported": ["S256"],
    }


@require_GET
def openid_configuration(request):
    return JsonResponse(_openid_configuration_payload(
        request, request.build_absolute_uri(reverse("sso:authorize")),
    ))


@require_GET
def openid_configuration_internal(request):
    """Discovery variant for relying parties INSIDE the compose network (e.g. the
    gitea container, which takes every endpoint from the discovery document with
    no overrides). token/userinfo/jwks are built from this request's (internal)
    host so the RP calls them server-side over plain HTTP — no public-cert trust
    needed. Only the authorization endpoint, the one URL a *browser* is redirected
    to, is forced to the public base from PLATFORM_DOMAIN. issuer comes from
    get_issuer like everywhere else (including the token endpoint that mints the
    ID token's `iss`), so issuer validation in the RP passes."""
    base = get_public_base_url()
    authorize = (
        f"{base}{reverse('sso:authorize')}" if base
        else request.build_absolute_uri(reverse("sso:authorize"))
    )
    return JsonResponse(_openid_configuration_payload(request, authorize))


@require_GET
def jwks(request):
    return JsonResponse(get_jwks())


@login_required
@require_GET
def authorize(request):
    response_type = request.GET.get("response_type")
    client_id = request.GET.get("client_id")
    redirect_uri = request.GET.get("redirect_uri")
    scope = request.GET.get("scope", "")
    state = request.GET.get("state")
    nonce = request.GET.get("nonce")
    consent = request.GET.get("consent")
    code_challenge = request.GET.get("code_challenge")
    code_challenge_method = request.GET.get("code_challenge_method")

    if response_type != "code":
        return HttpResponseBadRequest("Only response_type=code is supported.")
    if "openid" not in scope.split():
        return HttpResponseBadRequest("SSO/OIDC login requires scope=openid.")

    try:
        client = SSORelyingParty.objects.get(client_id=client_id, active=True)
    except SSORelyingParty.DoesNotExist:
        return HttpResponseBadRequest("Invalid client_id.")

    admin_test_uri = request.build_absolute_uri(reverse("sso:admin_test_callback"))
    is_admin_test = (redirect_uri == admin_test_uri and request.user.is_staff)
    if not redirect_uri or (not client.is_redirect_uri_allowed(redirect_uri) and not is_admin_test):
        return HttpResponseBadRequest("Invalid redirect_uri.")

    requested_scopes = set(scope.split())
    allowed_scopes = set(client.scope_list())
    if not requested_scopes.issubset(allowed_scopes):
        return HttpResponseBadRequest("Requested scope is not allowed for this client.")

    if client.client_type == SSORelyingParty.PUBLIC and not code_challenge:
        return HttpResponseBadRequest("Public clients must use PKCE.")

    if client.trusted or consent == "approved":
        return issue_authorization_code(
            request=request,
            client=client,
            redirect_uri=redirect_uri,
            scope=scope,
            state=state,
            nonce=nonce,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
        )

    # Decorated, like every other page. sso/consent.html extends oya/base.html,
    # and the palette lives in the Platform record rather than a stylesheet — so
    # a bare render left the consent screen unstyled, and its own
    # `{{ platform.site_name }}` blank. That is the one page where a visitor is
    # being asked to trust this site with their identity.
    from toto.ui import PageProcessor

    return render(request, "sso/consent.html", PageProcessor().decorate({
        "client": client,
        "scope_items": scope.split(),
        "query_string": request.META.get("QUERY_STRING", ""),
    }, request))


@login_required
@require_POST
def consent(request):
    decision = request.POST.get("decision")
    query_string = request.POST.get("query_string", "")
    if decision != "approve":
        return HttpResponseForbidden("Consent denied.")
    return redirect(f"{reverse('sso:authorize')}?{query_string}&consent=approved")


def issue_authorization_code(*, request, client, redirect_uri, scope, state, nonce, code_challenge=None, code_challenge_method=None):
    auth_code = SSOAuthorizationCode.objects.create(
        client=client,
        user=request.user,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
        nonce=nonce,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )
    params = {"code": auth_code.code}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return redirect(f"{redirect_uri}{separator}{urlencode(params)}")


def get_client_credentials(request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            raw = base64.b64decode(auth.removeprefix("Basic ").strip()).decode()
            client_id, client_secret = raw.split(":", 1)
            return client_id, client_secret
        except Exception:
            return None, None
    return request.POST.get("client_id"), request.POST.get("client_secret")


@csrf_exempt
@require_POST
def enroll(request):
    """The act of federation: a platform redeems a pairing code for credentials.

    The only endpoint in this feature, and the only new network surface on either
    host — the consumer exposes nothing, because both legs run consumer→provider.

    Unauthenticated by construction: the pairing code IS the authentication, the
    same way ``/sso/token/`` is authenticated by the authorization code. It sits
    under ``/sso/`` so it inherits nginx's ``limit_req zone=sso_auth burst=20``
    rather than needing a rate limiter of its own; the tree's only other throttle
    is session-based and useless to a machine caller.

    Refuses plaintext outside DEBUG, and is deliberately NOT added to
    ``SECURE_REDIRECT_EXEMPT`` — unlike ``/sso/token/``, which sidecars call over
    the internal plaintext port, nothing legitimate reaches this from inside the
    compose network.
    """
    import json

    from .enrollment import EnrollmentError, redeem

    if not request.is_secure():
        from django.conf import settings

        if not settings.DEBUG:
            return JsonResponse(
                {"error": "insecure_transport",
                 "detail": "Federation pairing requires HTTPS."},
                status=400,
            )

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "bad_request", "detail": "Expected a JSON body."}, status=400,
        )
    if not isinstance(payload, dict):
        return JsonResponse(
            {"error": "bad_request", "detail": "Expected a JSON object."}, status=400,
        )

    try:
        grant = redeem(payload, source_ip=_client_ip(request))
    except EnrollmentError as exc:
        # 401 for "your code is no good", 400 for "your request is malformed".
        # Never echoes the ticket back, and the detail is written for the operator
        # reading it on the other platform's screen.
        status = 400 if exc.code == "bad_request" else 401
        return JsonResponse({"error": exc.code, "detail": exc.message}, status=status)

    return JsonResponse(grant.to_dict())


def _client_ip(request):
    """Best-effort source IP for the audit row.

    Behind nginx the peer is always the proxy, so the forwarded header is the only
    thing with the real address in it. Recorded for after-the-fact review only —
    nothing authorises on it, so a spoofed header misleads a reader rather than
    granting anything.
    """
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")
    candidate = (forwarded[0] if forwarded else "").strip()
    return candidate or request.META.get("REMOTE_ADDR") or None


def _note_secret_proven(client, outcome):
    """Close the rotation grace window the first time the new secret is used.

    Called only from inside ``token()``'s transaction, and only after every other
    check has passed, so a failed exchange never moves it.

    ``"current"`` means the far side has demonstrably picked up the new secret, so
    the old one is no longer needed and is dropped immediately rather than waiting
    out its 24 hours — a rotation is only exposed for as long as it actually takes
    the peer to notice.

    ``"previous"`` means the peer is still presenting the old secret. Nothing is
    stamped: the window must stay open, and ``previous_secret_used_at`` is what
    lets the admin say "the other side has not picked up the new secret yet"
    instead of the operator having to guess.
    """
    from django.utils import timezone

    fields = []
    if outcome == "current":
        if client.secret_proven_at is None:
            client.secret_proven_at = timezone.now()
            fields.append("secret_proven_at")
        if client.previous_secret_hash or client.previous_secret_expires_at:
            client.previous_secret_hash = ""
            client.previous_secret_expires_at = None
            fields += ["previous_secret_hash", "previous_secret_expires_at"]
    elif outcome == "previous":
        client.previous_secret_used_at = timezone.now()
        fields.append("previous_secret_used_at")

    if fields:
        # update_fields so this can never clobber a concurrent write to an
        # unrelated column on the same row.
        client.save(update_fields=fields)


@csrf_exempt
@require_POST
def token(request):
    grant_type = request.POST.get("grant_type")
    code_value = request.POST.get("code")
    redirect_uri = request.POST.get("redirect_uri")
    code_verifier = request.POST.get("code_verifier")

    if grant_type != "authorization_code":
        return JsonResponse({"error": "unsupported_grant_type"}, status=400)

    client_id, client_secret = get_client_credentials(request)
    try:
        client = SSORelyingParty.objects.get(client_id=client_id, active=True)
    except SSORelyingParty.DoesNotExist:
        return JsonResponse({"error": "invalid_client"}, status=401)

    # The code is looked up and bound to the claimed client BEFORE the secret is
    # verified, and the order is deliberate. verify_client_secret is PBKDF2 at
    # Django's default 600k iterations — ~114 ms of CPU — and this endpoint is
    # csrf-exempt and unauthenticated by construction. Verifying first meant
    # anyone could spend 114 ms of a worker per request with nothing but a
    # guessable client_id ("grafana", "gitea"), and roughly 35 req/s saturated
    # the whole host, not just SSO.
    #
    # Reordering costs nothing: an authorization code is token_urlsafe(48),
    # single-use and five minutes old, so requiring one that already belongs to
    # the claimed client is not a bar a caller can clear without having been
    # issued it. The two indexed lookups below are microseconds.
    try:
        auth_code = SSOAuthorizationCode.objects.select_related("client", "user").get(code=code_value)
    except SSOAuthorizationCode.DoesNotExist:
        return JsonResponse({"error": "invalid_grant"}, status=400)

    if auth_code.client_id != client.id:
        return JsonResponse({"error": "invalid_grant"}, status=400)

    # "current", "previous" or None. This is the ONLY writer of secret_proven_at,
    # and without it the whole rotation grace window is dead code: nothing else
    # ever sets that field, so rotate_client_secret's guard never fires,
    # previous_secret_hash is never populated, and re-pairing a live federation
    # kills every session the instant the new secret is hashed.
    secret_outcome = client.check_client_secret(client_secret)
    if secret_outcome is None:
        return JsonResponse({"error": "invalid_client"}, status=401)

    if auth_code.redirect_uri != redirect_uri:
        return JsonResponse({"error": "invalid_grant"}, status=400)
    if auth_code.is_used or auth_code.is_expired:
        return JsonResponse({"error": "invalid_grant"}, status=400)
    if not verify_pkce(code_verifier, auth_code.code_challenge, auth_code.code_challenge_method):
        return JsonResponse({"error": "invalid_grant"}, status=400)

    # Atomic: a crash after mark_used() (e.g. build_id_token with no active
    # signing key) would otherwise burn the single-use code — the relying
    # party's auth-style retry then gets a misleading invalid_grant while the
    # real error hides in the first attempt's 500. Roll the consumption back
    # instead so a retry can succeed once the cause is fixed.
    with transaction.atomic():
        auth_code.mark_used()
        _note_secret_proven(client, secret_outcome)
        access_token = SSOAccessToken.objects.create(client=client, user=auth_code.user, scope=auth_code.scope)
        id_token = build_id_token(
            request,
            user=auth_code.user,
            client=client,
            scope=auth_code.scope,
            nonce=auth_code.nonce,
        )

    return JsonResponse({
        "access_token": access_token.token,
        "token_type": "Bearer",
        "expires_in": 3600,
        "id_token": id_token,
        "scope": auth_code.scope,
    })


@require_GET
def userinfo(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"error": "invalid_token"}, status=401)

    token_value = auth.removeprefix("Bearer ").strip()
    try:
        token = SSOAccessToken.objects.select_related("user").get(token=token_value)
    except SSOAccessToken.DoesNotExist:
        return JsonResponse({"error": "invalid_token"}, status=401)

    if token.is_expired or token.is_revoked:
        return JsonResponse({"error": "invalid_token"}, status=401)

    return JsonResponse(get_user_claims(token.user, token.scope.split()))


@login_required
def admin_test_login(request, pk):
    """Initiate an OIDC test flow for a relying party from the Django admin."""
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    try:
        client = SSORelyingParty.objects.get(pk=pk, active=True)
    except SSORelyingParty.DoesNotExist:
        return HttpResponseBadRequest("Client not found or inactive.")

    import secrets as _secrets
    state = _secrets.token_urlsafe(16)
    request.session["sso_admin_test_state"] = state
    request.session["sso_admin_test_client_pk"] = str(pk)

    callback_uri = request.build_absolute_uri(reverse("sso:admin_test_callback"))
    params = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": callback_uri,
        "scope": client.allowed_scopes,
        "state": state,
    }
    return redirect(f"{reverse('sso:authorize')}?{urlencode(params)}")


@login_required
def admin_test_callback(request):
    """Receive the code from the admin test OIDC flow and display the resulting claims."""
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")

    error = request.GET.get("error")
    if error:
        return render(request, "sso/admin_test_result.html", {"error": error})

    state = request.GET.get("state")
    stored_state = request.session.pop("sso_admin_test_state", None)
    client_pk = request.session.pop("sso_admin_test_client_pk", None)

    if not state or state != stored_state:
        return render(request, "sso/admin_test_result.html", {"error": "State mismatch — possible CSRF."})

    code_value = request.GET.get("code")
    if not code_value:
        return render(request, "sso/admin_test_result.html", {"error": "Missing code."})

    try:
        auth_code = SSOAuthorizationCode.objects.select_related("client", "user").get(
            code=code_value, client__pk=client_pk
        )
    except SSOAuthorizationCode.DoesNotExist:
        return render(request, "sso/admin_test_result.html", {"error": "Authorization code not found."})

    if auth_code.is_used or auth_code.is_expired:
        return render(request, "sso/admin_test_result.html", {"error": "Code already used or expired."})

    auth_code.mark_used()
    access_token = SSOAccessToken.objects.create(
        client=auth_code.client, user=auth_code.user, scope=auth_code.scope
    )
    claims = get_user_claims(auth_code.user, auth_code.scope.split())

    return render(request, "sso/admin_test_result.html", {
        "client": auth_code.client,
        "user": auth_code.user,
        "claims": claims,
        "scope": auth_code.scope,
        "access_token": access_token.token,
    })


@login_required
def my_profile(request):
    profile = getattr(request.user, "community_profile", None)
    if profile is not None:
        try:
            return redirect("socialhub:profile_details", slug=profile.slug)
        except NoReverseMatch:
            pass
    return redirect("core:dashboard")


# ---------------------------------------------------------------------------
# Federation: the platform-info API (for a federated patron) and the staff console.
# ---------------------------------------------------------------------------


@csrf_exempt
def platform_info(request):
    """This platform's public identity — name and logo — for a FEDERATED PATRON.

    A paired relying party authenticates exactly as at ``/sso/token/``:
    ``client_id`` + ``client_secret``, HTTP Basic (natural for GET) or POST body
    (``client_secret_post``), via the shared :func:`get_client_credentials`. This is
    the one durable credential pairing produces, so it is the right gate for "only a
    platform we are federated with may read this."

    A **public** client is refused: it holds no secret at all (``check_client_secret``
    returns ``"current"`` for it by design, to make PKCE-only OIDC correct), so it is
    not a patron in the "holds the shared secret" sense this endpoint means.
    """
    if request.method not in ("GET", "POST"):
        return HttpResponseBadRequest("Use GET or POST.")

    client_id, client_secret = get_client_credentials(request)
    if not client_id:
        return JsonResponse({"error": "invalid_client"}, status=401)
    try:
        client = SSORelyingParty.objects.get(client_id=client_id, active=True)
    except SSORelyingParty.DoesNotExist:
        return JsonResponse({"error": "invalid_client"}, status=401)
    if client.client_type == SSORelyingParty.PUBLIC:
        return JsonResponse({"error": "invalid_client"}, status=401)
    if client.check_client_secret(client_secret) not in ("current", "previous"):
        return JsonResponse({"error": "invalid_client"}, status=401)

    from toto.core.models import Platform

    platform = Platform.objects.filter(active=True).order_by("id").first()
    if platform is None:
        return JsonResponse({"error": "no_active_platform"}, status=404)

    def _abs(image_field):
        # Absolute so a remote patron can fetch it; None when unset. .url raises if
        # the field has no file, so guard it.
        try:
            return request.build_absolute_uri(image_field.url) if image_field else None
        except Exception:                       # noqa: BLE001
            return None

    fed = platform.federation
    return JsonResponse({
        "site_name": platform.site_name,
        "domain": platform.domain or "",
        "author": platform.author or "",
        "publication_year": platform.publication_year,
        "logo_url": _abs(platform.logo),
        "federation": (
            {"name": fed.name, "logo_url": _abs(fed.logo)} if fed else None
        ),
    })


def _minted_context(minted):
    """The QR panel context, matching the admin pair page's `minted` shape."""
    from toto.sso_core import qr

    return {
        "ticket": minted.ticket,
        "qr": qr.render_data_uri(minted.ticket),
        "seconds": minted.invite.seconds_remaining(),
        "host": minted.invite.expected_host,
        "scopes": minted.invite.granted_scopes,
    }


def _federation_rows():
    """The platforms federated with us, for the console list — local data only."""
    rows = []
    for party in SSORelyingParty.objects.order_by("-pairing_managed", "name"):
        host = ""
        for uri in party.redirect_uri_list():
            candidate = urlparse(uri).hostname or ""
            if candidate:
                host = candidate
                break
        last_token = (
            party.access_tokens.order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        if not party.pairing_managed:
            status = "sidecar"                  # Gitea/Grafana — not a pairing
        elif party.paired_at:
            status = "paired"
        else:
            status = "invited"
        rows.append({
            "pk": party.pk,
            "name": party.name,
            "client_id": party.client_id,
            "host": host,
            "active": party.active,
            "trusted": party.trusted,
            "status": status,
            "pairing_managed": party.pairing_managed,
            "last_token": last_token,
        })
    return rows


@login_required
def federation_console(request):
    """Staff page: the platforms federated with us, with a QR to (re-)pair one.

    A dedicated list surface for what the relying-party admin only exposes through
    object-tools. Local data only — it never calls a peer. Reuses ``enrollment.mint``
    (fresh invite, or re-pair in place via ``relying_party=``) and the same QR the
    admin pairing page renders.
    """
    from datetime import timedelta

    from django.core.exceptions import PermissionDenied

    from toto.ui import PageProcessor

    from .enrollment import EnrollmentError, mint
    from .models import (
        DEFAULT_INVITE_TTL_MINUTES,
        MAX_INVITE_TTL_MINUTES,
        MIN_INVITE_TTL_MINUTES,
    )

    if not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied

    minted = None
    error = None

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "repair":
                party = SSORelyingParty.objects.filter(
                    pk=request.POST.get("pk"), pairing_managed=True,
                ).first()
                if party is None:
                    error = "No such federated platform to re-pair."
                else:
                    host = ""
                    for uri in party.redirect_uri_list():
                        candidate = urlparse(uri).hostname or ""
                        if candidate:
                            host = candidate
                            break
                    minted = _minted_context(mint(
                        expected_host=host or party.name,
                        provider_url=get_public_base_url(),
                        label=party.name,
                        scopes=party.allowed_scopes,
                        trusted=party.trusted,
                        created_by=request.user,
                        relying_party=party,
                    ))
            elif action == "invite":
                roles = bool(request.POST.get("roles"))
                trusted = bool(request.POST.get("trusted"))
                try:
                    minutes = int(request.POST.get("ttl_minutes") or DEFAULT_INVITE_TTL_MINUTES)
                except (TypeError, ValueError):
                    minutes = DEFAULT_INVITE_TTL_MINUTES
                minutes = max(MIN_INVITE_TTL_MINUTES, min(MAX_INVITE_TTL_MINUTES, minutes))
                scopes = "openid email profile" + (" roles" if roles else "")
                minted = _minted_context(mint(
                    expected_host=(request.POST.get("expected_host") or "").strip(),
                    provider_url=get_public_base_url(),
                    label=(request.POST.get("expected_host") or "").strip(),
                    scopes=scopes,
                    trusted=trusted,
                    ttl=timedelta(minutes=minutes),
                    created_by=request.user,
                ))
        except EnrollmentError as exc:
            error = exc.message

    context = PageProcessor().decorate({
        "page_title": "Federation",
        "platforms": _federation_rows(),
        "minted": minted,
        "error": error,
        "min_ttl": MIN_INVITE_TTL_MINUTES,
        "max_ttl": MAX_INVITE_TTL_MINUTES,
        "default_ttl": DEFAULT_INVITE_TTL_MINUTES,
    }, request)
    return render(request, "sso_master/federation_console.html", context)


