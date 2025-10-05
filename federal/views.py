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


def _get_next(request):
    next_url = request.GET.get('next')
    if next_url:
        return next_url
    return 'nest:dashboard'


@csrf_protect
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

            # Token Issuance
            link = user.federated_links.filter(active=True).first()
            if link:
                federated_identity = link.federated_user
                provider = LocalIdentityProvider.objects.filter(active=True).first()
                if provider:
                    token = TokenService(provider).issue_token(federated_identity)
                    request.session["access_token"] = token.value
                    context["token"] = token.value
            return redirect(_get_next(request))

        context["error"] = "Invalid credentials."

    return render(request, "federal/login.html", processor.decorate(context, request))

def logout_view(request):
    logout(request)
    return redirect(_get_next(request))

def federal_welcome_view(request):
    token = request.session.get("access_token")
    context = {
        "page_title": "Welcome",
        "token": token,
    }
    processor = PageProcessor()
    return render(request, "federal/welcome.html", processor.decorate(context, request))

