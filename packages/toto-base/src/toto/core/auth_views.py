"""The single username/password login implementation.

``core:login`` and ``sso:login`` render different templates but must behave
identically; both delegate here so the two entry points cannot drift.
"""
import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse

from toto.core.auth_cooldown import (
    clear_login_retry_cooldown,
    login_retry_cooldown_remaining,
    login_retry_cooldown_seconds,
    start_login_retry_cooldown,
)
from toto.core.email_config import email_delivery_configured
from toto.core.forms import LoginForm
from toto.ui import PageProcessor

logger = logging.getLogger(__name__)


def _password_reset_url():
    # The reset flow ships with sso_master; consumer/local hosts don't mount it.
    try:
        return reverse("sso:password_reset")
    except NoReverseMatch:
        return ""


def password_login_view(request, *, template_name, page_title, extra_context=None):
    processor = PageProcessor()
    next_url = request.GET.get("next") or request.POST.get("next") or ""
    form = LoginForm(request.POST or None)
    password_reset_url = _password_reset_url()
    context = {
        "form": form,
        "page_title": page_title,
        "next": next_url,
        # Password reset needs a working email backend and the sso_master
        # urlconf; without either the "Forgot password?" link would dead-end,
        # so it is hidden.
        "password_reset_available": bool(password_reset_url) and email_delivery_configured(),
        "password_reset_url": password_reset_url,
    }
    if extra_context:
        context.update(extra_context)

    if request.user.is_authenticated:
        return redirect(next_url or reverse("core:dashboard"))

    if request.method == "POST":
        remaining = login_retry_cooldown_remaining(request)
        if remaining > 0:
            context["error"] = f"Please wait {remaining} seconds before trying again."
            context["cooldown_remaining"] = remaining
            messages.error(request, context["error"])
            return render(request, template_name, processor.decorate(context, request))

    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"],
        )
        if user:
            clear_login_retry_cooldown(request)
            login(request, user)
            logger.info(f"User '{user.username}' logged in successfully.")
            return redirect(next_url or reverse("core:dashboard"))
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

    return render(request, template_name, processor.decorate(context, request))


def password_logout_view(request):
    if request.user.is_authenticated:
        logger.info(f"User '{request.user.username}' logged out.")
    logout(request)
    next_url = request.GET.get("next") or ""
    return redirect(next_url or reverse("core:dashboard"))
