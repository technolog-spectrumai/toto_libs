from toto.core.models import Platform
from toto.core.page import PageProcessor
from django.contrib.auth import get_user_model
from toto.core.models import Platform
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
import logging
from toto.core.forms import LoginForm
import os
from django.conf import settings
from django.urls import reverse, NoReverseMatch


logger = logging.getLogger(__name__)
User = get_user_model()


template_dir = "oya"


def _get_template(name):
    return os.path.join(template_dir, name)


def welcome_view(request):
    processor = PageProcessor()

    platform = Platform.objects.filter(active=True).first()

    context = {
        "platform": platform,
        "federation": platform.federation if platform else None
    }

    return render(request, _get_template("home.html"), processor.decorate(context, request))


def dashboard_view(request):
    processor = PageProcessor()

    items = []

    for item in settings.DASHBOARD_ITEMS:
        # Skip non-public items for anonymous users
        if not request.user.is_authenticated and not item.get("public", True):
            continue

        # Resolve named URLs like "events:event_list"
        link = item.get("link")
        if link and ":" in link:
            try:
                link = reverse(link)
            except NoReverseMatch:
                pass  # allow raw URLs

        items.append({
            "title": item["title"],
            "description": item["description"],
            "icon": item["icon"],
            "link": link,
            "public": item.get("public", True),
        })

    context = {
        "page_title": "Dashboard",
        "blocks": items,
    }

    context = processor.decorate(context, request)

    return render(request, _get_template("dashboard.html"), context)



def not_implemented(request):
    processor = PageProcessor()
    context = {
        "page_title": "Not Implemented"
    }
    return render(request, _get_template("placeholder.html"), processor.decorate(context, request))


def maintenance_view(request):
    processor = PageProcessor(maintenance_mode=True)
    context = {"page_title": "Under Maintenance"}
    return render(request, _get_template("maintenance.html"), processor.decorate(context, request))


def _get_next(request):
    return request.GET.get('next') or 'core:dashboard'


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

    return render(request,"oya/login.html", processor.decorate(context, request))


def logout_view(request):
    logger.info(f"User '{request.user.username}' logged out.")
    logout(request)
    return redirect(_get_next(request))









