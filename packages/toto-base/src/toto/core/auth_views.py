"""The single username/password login implementation.

``core:login`` and ``sso:login`` render different templates but must behave
identically; both delegate here so the two entry points cannot drift.
"""
import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_POST

from toto.core.auth_cooldown import (
    clear_login_retry_cooldown,
    login_retry_cooldown_remaining,
    login_retry_cooldown_seconds,
    start_login_retry_cooldown,
)
from toto.core.forms import LoginForm
from toto.core.safe_next import safe_next
from toto.core.signin_lockout import refusal_for
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
    # Only a place on this site (2026-09-30): an off-site `next` made this
    # genuine page the first step of a phishing round trip. The page's hidden
    # field carries the checked value too, so a refused one is dropped.
    next_url = safe_next(request, request.GET.get("next") or request.POST.get("next"))
    form = LoginForm(request.POST or None)
    password_reset_url = _password_reset_url()
    context = {
        "form": form,
        "page_title": page_title,
        "next": next_url,
        # Password reset needs only the sso_core urlconf now: with no working
        # email backend the same page serves patron-authorized recovery
        # instead (sso_core.password_reset's two flows), so the link stops
        # dead-ending the moment the route exists. Local mode still mounts no
        # such route and still hides the link.
        "password_reset_available": bool(password_reset_url),
        "password_reset_url": password_reset_url,
    }
    if extra_context:
        context.update(extra_context)
    context.setdefault("social_providers", _social_login_providers(request))

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
            logger.info("Account %s signed in.", user.pk)
            return redirect(next_url or reverse("core:dashboard"))
        held = refusal_for(request)
        if held is not None:
            # The sign-in lockout (2026-09-30): no password was compared. Say
            # how long is left — the same sentence whatever the name — and let
            # the page count it down instead of the 3-second cooldown.
            logger.warning("Sign-in refused by the lockout (%s).", held.reason)
            context["error"] = held.message
            context["cooldown_remaining"] = held.retry_after
            messages.error(request, context["error"])
            return render(request, template_name, processor.decorate(context, request))
        # Not the name typed (2026-10-01): it is often an e-mail address, now
        # and then a password typed into the wrong box. AUTH.LOGIN_FAILED on
        # the audit chain names it for those who may read it.
        logger.warning("Failed sign-in attempt.")
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


def _social_login_providers(request):
    # Soft edge by design: toto-base must not require toto-auth. The social
    # app declares the concrete providers; without it there are no buttons.
    from django.apps import apps

    if not apps.is_installed("toto.social_login"):
        return []
    from toto.social_login.providers import login_page_providers

    return login_page_providers(request)


# POST only, with the CSRF token (2026-10-01, Django 5.2): a GET sign-out is
# one any page could make a member's browser take — an <img> is enough —
# and Django's own LogoutView stopped accepting GET in 5.0 for that reason.
# The header's Logout is a form now; a GET is answered 405.
@require_POST
def password_logout_view(request):
    if request.user.is_authenticated:
        logger.info("Account %s signed out.", request.user.pk)
    logout(request)
    next_url = safe_next(request, request.POST.get("next") or request.GET.get("next"))
    return redirect(next_url or reverse("core:dashboard"))
