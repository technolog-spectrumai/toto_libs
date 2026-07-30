import base64
from urllib.parse import urlencode

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
        "code_challenge_methods_supported": ["plain", "S256"],
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

    return render(request, "sso/consent.html", {
        "client": client,
        "scope_items": scope.split(),
        "query_string": request.META.get("QUERY_STRING", ""),
    })


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

    if not client.verify_client_secret(client_secret):
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


