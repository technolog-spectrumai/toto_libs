from federal.models import FederatedIdentity
from gate.token import TokenGuard
from gate.challenge import ChallengeGuard
from django.contrib.auth import get_user_model
from oya.models import Platform
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from community.models import MembershipApplication, generate_code, CommunityMember, Community
from community.forms import LoginForm, MembershipApplicationForm, CodeVerificationForm, ReferenceRequestForm
from .forms import ChallengeIdentityForm, ChallengeSignatureForm
from oya.page import PageProcessor
import logging
from django.http import HttpRequest, HttpResponse


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
    return 'root:dashboard' #request.GET.get('next') or


# @csrf_exempt
# def initiate_login_view(request, identity_id):
#     """
#     Step 1: Issue a new challenge for the given identity.
#     """
#     identity = get_object_or_404(FederatedIdentity, pk=identity_id)
#     challenge = ChallengeGuard.initiate_login(identity)
#
#     data = {
#         "challenge_id": str(challenge.id),
#         "identity_id": str(identity.id),
#         "issued_at": challenge.issued_at.isoformat(),
#         "nonce": challenge.nonce,
#     }
#     return JsonResponse(data)
#
#
# @csrf_exempt
# def verify_login_view(request, identity_id):
#     """
#     Step 2: Verify the challenge with the signature.
#     If successful, issue access + refresh tokens.
#     """
#     identity = get_object_or_404(FederatedIdentity, pk=identity_id)
#
#     signature_b64 = request.POST.get("signature")
#     if not signature_b64:
#         return JsonResponse({"error": "Missing signature"}, status=400)
#
#     if ChallengeGuard.verify_login(identity, signature_b64):
#         secret_key = get_platform_secret()
#         token_guard = TokenGuard(secret_key)
#         access_token = token_guard.issue_access_token(identity)
#         refresh_token = token_guard.issue_refresh_token(identity)
#         return JsonResponse({
#             "access_token": access_token,
#             "refresh_token": refresh_token,
#         })
#     return JsonResponse({"error": "Verification failed"}, status=403)
#
#
# @csrf_exempt
# def refresh_token_view(request):
#     """
#     Step 3: Refresh an access token using a valid refresh token.
#     """
#     refresh_token = request.POST.get("refresh_token")
#     if not refresh_token:
#         return JsonResponse({"error": "Missing refresh_token"}, status=400)
#
#     try:
#         user = TokenGuard.get_user_from_refresh_token(refresh_token)
#     except jwt.ExpiredSignatureError:
#         return JsonResponse({"error": "Token expired"}, status=400)
#     except jwt.DecodeError:
#         return JsonResponse({"error": "Invalid token"}, status=400)
#     except User.DoesNotExist:
#         return JsonResponse({"error": "User not found"}, status=404)
#
#     secret_key = get_platform_secret()
#     guard = TokenGuard(secret_key)
#     new_access = guard.refresh_access_token(refresh_token)
#     if not new_access:
#         return JsonResponse({"error": "Refresh failed"}, status=403)
#
#     return JsonResponse({"access_token": new_access})


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


def challenge_identity_view(request: HttpRequest) -> HttpResponse:
    """
    Step 1: Submit identity_id → initiate challenge and redirect to signature step
    """
    processor = PageProcessor()

    if request.method == "POST":
        form = ChallengeIdentityForm(request.POST)
        if form.is_valid():
            identity_id = form.cleaned_data["identity_id"]
            identity = get_object_or_404(FederatedIdentity, pk=identity_id)

            # Initiate challenge and store it (e.g. in session)
            challenge = ChallengeGuard.initiate_login(identity)
            request.session["identity_id"] = str(identity.id)
            request.session["challenge_nonce"] = str(challenge.nonce)

            return redirect("gate:challenge_signature")

    else:
        form = ChallengeIdentityForm()

    context = {"id_form": form}
    return render(request, "gate/challenge_identity.html", processor.decorate(context, request))


def challenge_signature_view(request: HttpRequest) -> HttpResponse:
    """
    Step 2: Show challenge + verify signature
    """
    processor = PageProcessor()
    identity_id = request.session.get("identity_id")
    nonce = request.session.get("challenge_nonce")

    if not identity_id or not nonce:
        # No challenge in session → go back to step 1
        return redirect("gate:challenge_identity")

    identity = get_object_or_404(FederatedIdentity, pk=identity_id)

    if request.method == "POST":
        form = ChallengeSignatureForm(request.POST)
        if form.is_valid():
            signature = form.cleaned_data["signature"]

            if ChallengeGuard.verify_login(identity, signature):
                logger.info(f"Challenge login successful for identity {identity.id}")
                # Clear session
                request.session.pop("challenge_nonce", None)
                request.session.pop("identity_id", None)
                return redirect(_get_next(request))
            else:
                context = {
                    "identity": identity,
                    "challenge": {"nonce": nonce},
                    "sig_form": form,
                    "error": "Verification failed.",
                }
                return render(request, "gate/challenge_signature.html", processor.decorate(context, request))
    else:
        sig_form = ChallengeSignatureForm(initial={"identity_id": identity_id})

    context = {
        "identity": identity,
        "challenge": {"nonce": nonce},
        "sig_form": sig_form,
    }
    return render(request, "gate/challenge_signature.html", processor.decorate(context, request))



