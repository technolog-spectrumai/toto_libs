"""Who may open Storage's superuser tabs (Management, Clearances).

A superuser on the Superuser plan — both, never one
(``toto.subscriptions.models.superuser_plan_active``). The vault is a free
app, so the app-level subscription gate cannot guard a tab; every door of
these tabs asks this instead, and the tab strip asks the same question
(``vault_flags.superuser_plan``), so a tab is never shown to someone its
doors would refuse.

A host without ``toto.subscriptions`` sells no plan: there, being a
superuser is enough (the dashboard's and the economy's rule,
``core.views._superuser_with_plan``, ``quota.rates.economy_operator``).
"""

from __future__ import annotations

from functools import wraps

from django.apps import apps
from django.http import HttpResponseForbidden, JsonResponse
from django.utils.translation import gettext as _


def superuser_plan_holder(user) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_superuser", False) or not getattr(user, "is_active", False):
        return False
    if not apps.is_installed("toto.subscriptions"):
        return True
    from toto.subscriptions.models import superuser_plan_active

    return superuser_plan_active(user)


def refusal_sentence() -> str:
    return _("This needs a superuser on the Superuser plan.")


def wants_json(request) -> bool:
    accept = request.headers.get("Accept", "")
    return ("application/json" in accept
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.content_type == "application/json")


def superuser_plan_door(view=None, *, json=False):
    """Refuse anyone but a superuser on the Superuser plan, BEFORE the view
    looks anything up (a slug-addressed door then answers the same for a
    bucket that exists and one that does not). Anonymous visitors of a page
    go to the login page; a JSON door (``json=True``, or a request asking
    for JSON) answers ``{"error": ...}`` with 403."""

    def decorate(func):
        @wraps(func)
        def wrapped(request, *args, **kwargs):
            user = getattr(request, "user", None)
            as_json = json or wants_json(request)
            if not getattr(user, "is_authenticated", False) and not as_json:
                from django.contrib.auth.views import redirect_to_login

                return redirect_to_login(request.get_full_path())
            if not superuser_plan_holder(user):
                if as_json:
                    response = JsonResponse({"ok": False, "error": refusal_sentence()}, status=403)
                    response["Cache-Control"] = "no-store"
                    return response
                return HttpResponseForbidden(refusal_sentence())
            return func(request, *args, **kwargs)

        wrapped.superuser_plan_required = True
        return wrapped

    return decorate(view) if view is not None else decorate
