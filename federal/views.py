# federal/views.py
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from federal.models import LocalIdentityProvider, FederatedIdentity
from federal.token import TokenService
import json


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
