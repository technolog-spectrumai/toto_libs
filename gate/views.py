import jwt
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from federal.models import FederatedIdentity
from gate.models import AuthGateway
from toto.gate.token import TokenGuard
from gate.challenge import ChallengeGuard
from django.contrib.auth import get_user_model
from oya.models import Platform


User = get_user_model()

def get_platform_secret():
    """
    Helper to fetch the active Platform's secret key.
    Adjust the lookup if you have multiple platforms.
    """
    platform = Platform.objects.filter(active=True).first()
    if not platform or not platform.secret:
        raise RuntimeError("No active Platform or SecretKey configured")
    return platform.secret.key


@csrf_exempt
def initiate_login_view(request, identity_id):
    """
    Step 1: Issue a new challenge for the given identity.
    """
    identity = get_object_or_404(FederatedIdentity, pk=identity_id)
    challenge = ChallengeGuard.initiate_login(identity)

    data = {
        "challenge_id": str(challenge.id),
        "identity_id": str(identity.id),
        "issued_at": challenge.issued_at.isoformat(),
        "nonce": challenge.nonce,
    }
    return JsonResponse(data)


@csrf_exempt
def verify_login_view(request, identity_id):
    """
    Step 2: Verify the challenge with the signature.
    If successful, issue access + refresh tokens.
    """
    identity = get_object_or_404(FederatedIdentity, pk=identity_id)

    signature_b64 = request.POST.get("signature")
    if not signature_b64:
        return JsonResponse({"error": "Missing signature"}, status=400)

    if ChallengeGuard.verify_login(identity, signature_b64):
        secret_key = get_platform_secret()
        token_guard = TokenGuard(secret_key)
        access_token = token_guard.issue_access_token(identity)
        refresh_token = token_guard.issue_refresh_token(identity)
        return JsonResponse({
            "access_token": access_token,
            "refresh_token": refresh_token,
        })
    return JsonResponse({"error": "Verification failed"}, status=403)


@csrf_exempt
def refresh_token_view(request):
    """
    Step 3: Refresh an access token using a valid refresh token.
    """
    refresh_token = request.POST.get("refresh_token")
    if not refresh_token:
        return JsonResponse({"error": "Missing refresh_token"}, status=400)

    try:
        user = TokenGuard.get_user_from_refresh_token(refresh_token)
    except jwt.ExpiredSignatureError:
        return JsonResponse({"error": "Token expired"}, status=400)
    except jwt.DecodeError:
        return JsonResponse({"error": "Invalid token"}, status=400)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    secret_key = get_platform_secret()
    guard = TokenGuard(secret_key)
    new_access = guard.refresh_access_token(refresh_token)
    if not new_access:
        return JsonResponse({"error": "Refresh failed"}, status=403)

    return JsonResponse({"access_token": new_access})


