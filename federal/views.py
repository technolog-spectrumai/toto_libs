from django.shortcuts import get_object_or_404, render

from .guard import FederalGuard
from .models import Federation
from .page import PageProcessor
import base64
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from federal.models import FederatedIdentity, Challenge, FederalAuthGateway



def federation_detail_json(request, pk):
    federation = get_object_or_404(Federation, pk=pk)
    data = {
        "id": federation.id,
        "name": federation.name,
        "created_at": federation.created_at.isoformat(),
        "active": federation.active,
        "url": federation.url,
    }
    return JsonResponse(data)

def federation_list_json(request):
    federations = Federation.objects.all()
    data = [
        {
            "id": f.id,
            "name": f.name,
            "created_at": f.created_at.isoformat(),
            "active": f.active,
            "url": f.url,
        }
        for f in federations
    ]
    return JsonResponse(data, safe=False)


def current_federation_view(request):
    """
    Render the current active federation that has an active platform.
    """
    federation = get_object_or_404(
        Federation,
        active=True,
        platform__active=True
    )
    context = {"federation": federation}
    context = PageProcessor().decorate(context, request)
    return render(request, "federal/current_federation.html", context)


@csrf_exempt
def initiate_login_view(request, identity_id):
    """
    Step 1: Issue a new challenge for the given identity.
    """
    identity = get_object_or_404(FederatedIdentity, pk=identity_id)
    gateway = get_object_or_404(FederalAuthGateway, federation=identity.federation)
    guard = FederalGuard(gateway)

    challenge = guard.initiate_login(identity)

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
    """
    identity = get_object_or_404(FederatedIdentity, pk=identity_id)
    gateway = get_object_or_404(FederalAuthGateway, federation=identity.federation)
    guard = FederalGuard(gateway)

    signature_b64 = request.POST.get("signature")
    if not signature_b64:
        return JsonResponse({"error": "Missing signature"}, status=400)

    if guard.verify_login(identity, signature_b64):
        access_token = guard.issue_access_token(identity)
        refresh_token = guard.issue_refresh_token(identity)
        return JsonResponse({
            "access_token": access_token,
            "refresh_token": refresh_token,
        })
    return JsonResponse({"error": "Verification failed"}, status=403)


@csrf_exempt
def refresh_token_view(request):
    refresh_token = request.POST.get("refresh_token")
    if not refresh_token:
        return JsonResponse({"error": "Missing refresh_token"}, status=400)

    # use guard helper
    gateway = None
    identity = None

    guard = None
    identity = FederalGuard.get_identity_from_refresh_token(FederalGuard, refresh_token)
    if not identity:
        return JsonResponse({"error": "Invalid token"}, status=400)

    gateway = get_object_or_404(FederalAuthGateway, federation=identity.federation)
    guard = FederalGuard(gateway)

    new_access = guard.refresh_access_token(refresh_token)
    if not new_access:
        return JsonResponse({"error": "Refresh failed"}, status=403)

    return JsonResponse({"access_token": new_access})


