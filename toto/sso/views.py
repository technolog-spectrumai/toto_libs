import base64
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import SSOAccessToken, SSOAuthorizationCode, SSOClient
from .services import build_id_token, get_issuer, get_jwks, get_subject_for_user, get_user_claims, verify_pkce


@require_GET
def openid_configuration(request):
    issuer = get_issuer(request)
    return JsonResponse({
        "issuer": issuer,
        "authorization_endpoint": request.build_absolute_uri(reverse("sso:authorize")),
        "token_endpoint": request.build_absolute_uri(reverse("sso:token")),
        "userinfo_endpoint": request.build_absolute_uri(reverse("sso:userinfo")),
        "jwks_uri": request.build_absolute_uri(reverse("sso:jwks")),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "profile"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "claims_supported": [
            "iss", "sub", "aud", "exp", "iat", "auth_time", "nonce",
            "email", "email_verified", "name", "preferred_username",
            "given_name", "family_name", "display_name", "person_slug",
        ],
        "code_challenge_methods_supported": ["plain", "S256"],
    })


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
        client = SSOClient.objects.get(client_id=client_id, active=True)
    except SSOClient.DoesNotExist:
        return HttpResponseBadRequest("Invalid client_id.")

    if not redirect_uri or not client.is_redirect_uri_allowed(redirect_uri):
        return HttpResponseBadRequest("Invalid redirect_uri.")

    requested_scopes = set(scope.split())
    allowed_scopes = set(client.scope_list())
    if not requested_scopes.issubset(allowed_scopes):
        return HttpResponseBadRequest("Requested scope is not allowed for this client.")

    if client.client_type == SSOClient.PUBLIC and not code_challenge:
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
        client = SSOClient.objects.get(client_id=client_id, active=True)
    except SSOClient.DoesNotExist:
        return JsonResponse({"error": "invalid_client"}, status=401)

    if not client.verify_client_secret(client_secret):
        return JsonResponse({"error": "invalid_client"}, status=401)

    try:
        auth_code = SSOAuthorizationCode.objects.select_related("client", "user").get(code=code_value)
    except SSOAuthorizationCode.DoesNotExist:
        return JsonResponse({"error": "invalid_grant"}, status=400)

    if auth_code.client_id != client.id:
        return JsonResponse({"error": "invalid_grant"}, status=400)
    if auth_code.redirect_uri != redirect_uri:
        return JsonResponse({"error": "invalid_grant"}, status=400)
    if auth_code.is_used or auth_code.is_expired:
        return JsonResponse({"error": "invalid_grant"}, status=400)
    if not verify_pkce(code_verifier, auth_code.code_challenge, auth_code.code_challenge_method):
        return JsonResponse({"error": "invalid_grant"}, status=400)

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
