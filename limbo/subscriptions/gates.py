"""
Subscription gates — check and enforce subscription access.

Usage:

    from toto.subscriptions.gates import is_subscribed, SubscriptionRequired

    # In a view:
    if not is_subscribed(request.user, "academy"):
        raise SubscriptionRequired("academy", "Academy")

    # As a decorator:
    @require_subscription("academy")
    def enroll(request, course_slug):
        ...

Rules
-----
- A user is subscribed to plan_code if they have an ACTIVE or TRIALING Subscription
  to any plan whose code matches (or starts with) the given plan_code.
- SubscriptionCustomer lookup is via user → person (community_profile).
- If the user has no community_profile, they are not subscribed to anything.
- Does NOT import tariffs, treasury, or budget.
"""
from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _


class SubscriptionRequired(Exception):
    """Raised when a subscription is required but not active."""

    def __init__(self, plan_code: str, plan_name: str = "", upgrade_url: str = ""):
        self.plan_code = plan_code
        self.plan_name = plan_name or plan_code
        self.upgrade_url = upgrade_url

    def __str__(self) -> str:
        return (
            f"A {self.plan_name} subscription is required to access this feature."
        )


_ACTIVE_STATUSES = {"active", "trialing"}


def is_subscribed(user, plan_code: str) -> bool:
    """
    Return True if the user has an active/trialing subscription to plan_code.
    Silently returns False on any error (missing profile, DB issue, etc.).
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    try:
        from toto.subscriptions.models import Subscription, SubscriptionCustomer
        person = user.community_profile
        customer_ids = SubscriptionCustomer.objects.filter(
            person=person, status="active"
        ).values_list("pk", flat=True)
        return Subscription.objects.filter(
            customer_id__in=customer_ids,
            plan__code__startswith=plan_code,
            status__in=_ACTIVE_STATUSES,
        ).exists()
    except Exception:
        return False


def require_subscription(plan_code: str, plan_name: str = "", redirect_to: str = ""):
    """
    Decorator for function-based views.

    If user is not subscribed:
    - Adds an error message explaining what subscription is needed.
    - Redirects to redirect_to if given, otherwise to subscriptions:plan_list.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not is_subscribed(request.user, plan_code):
                name = plan_name or plan_code.capitalize()
                messages.error(
                    request,
                    _(f"A {name} subscription is required. "
                      f"Subscribe to continue."),
                )
                target = redirect_to
                if not target:
                    try:
                        from django.urls import reverse
                        target = reverse("subscriptions:plan_list")
                    except Exception:
                        target = "/"
                return redirect(target)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
