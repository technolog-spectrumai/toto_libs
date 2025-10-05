# federal/views.py
from federal.models import LocalIdentityProvider, FederatedIdentity
from federal.token import TokenService
import json
from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .forms import LoginForm
from .page import PageProcessor
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_protect


@csrf_exempt
def issue_token_view(request):
    if request.method != "POST":
        return JsonResponse({"error": "Only POST allowed"}, status=405)

    try:
        data = json.loads(request.body)
        provider_id = data["provider_id"]
        federated_id = data["federated_id"]

        provider = LocalIdentityProvider.objects.get(id=provider_id)
        federated_identity = FederatedIdentity.objects.get(id=federated_id)

        service = TokenService(provider)
        token = service.issue_token(federated_identity)

        return JsonResponse(token.serialize())

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def verify_token_view(request):
    if request.method != "POST":
        return JsonResponse({"error": "Only POST allowed"}, status=405)

    try:
        data = json.loads(request.body)
        token_str = data["token"]
        provider_id = data["provider_id"]

        provider = LocalIdentityProvider.objects.get(id=provider_id)
        service = TokenService(provider)
        decoded = service.verify_token(token_str)

        if not decoded:
            return JsonResponse({"active": False}, status=401)

        return JsonResponse({"active": True, "claims": decoded})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


def federal_welcome_view(request):
    token = request.session.get("access_token")
    processor = PageProcessor()
    platform = getattr(processor, "platform", None)
    logo_url = None
    identity_provider = LocalIdentityProvider.objects.filter(active=True).first()
    federation = getattr(identity_provider, "federation", None) if identity_provider else None
    if federation and getattr(federation, "logo", None):
        logo_url = federation.logo.url

    context = {
        "page_title": "Welcome",
        "token": token,
        "platform": platform,
        "logo_url": logo_url,
    }

    return render(request, "federal/welcome.html", processor.decorate(context, request))



