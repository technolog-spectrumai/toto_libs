import jwt
from django.views.decorators.csrf import csrf_exempt
from federal.models import FederatedIdentity
from gate.token import TokenGuard
from gate.challenge import ChallengeGuard
from django.contrib.auth import get_user_model
from oya.models import Platform
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.contrib.auth.models import User
from community.models import MembershipApplication, generate_code, CommunityMember, Community
from community.forms import LoginForm, MembershipApplicationForm, CodeVerificationForm, ReferenceRequestForm

from .forms import ChallengeLoginForm
from .page import PageProcessor
import logging

logger = logging.getLogger(__name__)
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


def _get_next(request):
    return request.GET.get('next') or 'root:dashboard'


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


def login_view(request):
    processor = PageProcessor()
    form = LoginForm(request.POST or None)
    context = {"form": form, "page_title": "Login"}

    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"]
        )
        if user:
            login(request, user)
            logger.info(f"User '{user.username}' logged in successfully.")
            return redirect(_get_next(request))
        else:
            logger.warning(f"Failed login attempt for username '{form.cleaned_data['username']}'.")
            context["error"] = "Invalid credentials."

    return render(request,"gate/login.html", processor.decorate(context, request))


def logout_view(request):
    logger.info(f"User '{request.user.username}' logged out.")
    logout(request)
    return redirect(_get_next(request))


def challenge_login_view(request):
    form = ChallengeLoginForm(request.POST or None)
    context = {"form": form}
    processor = PageProcessor()
    if request.method == "POST" and form.is_valid():
        identity_id = form.cleaned_data["identity_id"]
        identity = get_object_or_404(FederatedIdentity, pk=identity_id)
        challenge = ChallengeGuard.initiate_login(identity)
        context.update({"identity": identity, "challenge": challenge})
    context = processor.decorate(context, request)
    return render(request, "gate/challenge.html", context)


@csrf_exempt
def challenge_verify_view(request):
    form = ChallengeLoginForm(request.POST or None)
    processor = PageProcessor()
    if request.method == "POST" and form.is_valid():
        identity_id = form.cleaned_data["identity_id"]
        signature_b64 = form.cleaned_data["signature"]
        identity = get_object_or_404(FederatedIdentity, pk=identity_id)

        if ChallengeGuard.verify_login(identity, signature_b64):
            logger.info(f"Challenge login successful for identity {identity.id}")
            return redirect(_get_next(request))
        else:
            context = {
                "form": form,
                "identity": identity,
                "error": "Verification failed. Please try again."
            }
            context = processor.decorate(context, request)
            return render(request, "gate/challenge.html", context)
    context = {"form": form, "error": "Invalid input."}
    context = processor.decorate(context, request)
    return render(request, "gate/challenge.html", context)


