from toto.core.models import Platform
from toto.ui import PageProcessor
from django.contrib import messages
from django.contrib.auth import get_user_model
from toto.core.models import Platform
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
import logging
from toto.core.forms import LoginForm
from toto.core.auth_cooldown import (
    clear_login_retry_cooldown,
    login_retry_cooldown_remaining,
    login_retry_cooldown_seconds,
    start_login_retry_cooldown,
)
import os
from django.conf import settings
from django.urls import reverse, NoReverseMatch
from django.utils.translation import override as translation_override


logger = logging.getLogger(__name__)
User = get_user_model()


template_dir = "oya"


def _get_template(name):
    return os.path.join(template_dir, name)


def _connect_url(platform):
    """The canonical, shareable address to encode in the welcome QR.

    Prefer the .onion (faros) so the QR works regardless of how the page is viewed;
    fall back to the configured public domain; else "" (the template uses the
    browser origin). Kept layering-safe: core never hard-depends on nomad.
    """
    from django.apps import apps  # noqa: PLC0415

    if apps.is_installed("toto.nomad"):
        try:
            from toto.nomad.service import current_onion  # noqa: PLC0415
            onion = current_onion()
            if onion:
                return f"https://{onion}.onion"
        except Exception:
            pass

    domain = (platform.domain or "").strip() if platform else ""
    if domain and domain not in ("localhost", "127.0.0.1"):
        return f"https://{domain}"
    return ""


def welcome_view(request):
    processor = PageProcessor()

    platform = Platform.objects.filter(active=True).first()

    context = {
        "platform": platform,
        "federation": platform.federation if platform else None,
        "connect_url": _connect_url(platform),
    }

    return render(request, _get_template("home.html"), processor.decorate(context, request))


def _resolve_dashboard_item(item, authenticated):
    visibility = item.get("visibility", "public")
    if visibility == "private" and not authenticated:
        return None
    link = item.get("link")
    if link and ":" in link:
        try:
            link = reverse(link)
        except NoReverseMatch:
            pass
    return {
        "title": item["title"],
        "description": item["description"],
        "icon": item["icon"],
        "link": link,
        "visibility": visibility,
    }


def _resolve_all_items(authenticated):
    items_by_key = {}
    for item in settings.DASHBOARD_ITEMS:
        resolved = _resolve_dashboard_item(item, authenticated)
        if resolved is not None:
            with translation_override("en"):
                en_key = str(item["title"])
            items_by_key[en_key] = resolved
    return items_by_key


def dashboard_view(request):
    processor = PageProcessor()
    authenticated = request.user.is_authenticated
    items_by_key = _resolve_all_items(authenticated)

    if authenticated:
        groups = []
        for category in settings.DASHBOARD_CATEGORIES:
            grouped_items = [
                items_by_key[title]
                for title in category["items"]
                if title in items_by_key
            ]
            if grouped_items:
                groups.append({"title": category["title"], "items": grouped_items})
        use_groups = True
    else:
        groups = [{"title": "", "items": list(items_by_key.values())}]
        use_groups = False

    total_items = sum(len(g["items"]) for g in groups)

    context = {
        "page_title": "Dashboard",
        "groups": groups,
        "use_groups": use_groups,
        "total_items": total_items,
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

    if request.method == "POST":
        remaining = login_retry_cooldown_remaining(request)
        if remaining > 0:
            context["error"] = f"Please wait {remaining} seconds before trying again."
            context["cooldown_remaining"] = remaining
            messages.error(request, context["error"])
            return render(request,"oya/login.html", processor.decorate(context, request))

    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"]
        )
        if user:
            clear_login_retry_cooldown(request)
            login(request, user)
            logger.info(f"User '{user.username}' logged in successfully.")
            return redirect(_get_next(request))
        else:
            logger.warning(f"Failed login attempt for username '{form.cleaned_data['username']}'.")
            context["error"] = "Invalid username or password."
            context["cooldown_remaining"] = login_retry_cooldown_seconds()
            messages.error(request, context["error"])
            start_login_retry_cooldown(request)
    elif request.method == "POST":
        context["error"] = "Enter your username and password."
        context["cooldown_remaining"] = login_retry_cooldown_seconds()
        messages.error(request, context["error"])
        start_login_retry_cooldown(request)

    return render(request,"oya/login.html", processor.decorate(context, request))


def logout_view(request):
    logger.info(f"User '{request.user.username}' logged out.")
    logout(request)
    return redirect(_get_next(request))






