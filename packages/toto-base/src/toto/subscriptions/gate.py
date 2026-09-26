"""One middleware, and the whole of what a plan enforces.

## Why middleware and not decorators

Delta's subscription app has a decorator, an exception and a helper. The
decorator is used **zero** times, the exception is raised **never**, and the one
gate in the entire platform is an inline block inside a single view. That is
what fifteen apps' worth of "remember to add the decorator" looks like after a
few months, and it is the honest reason this is a middleware: there is one
place to get right, it cannot be forgotten when a view is added, and **the API
is covered by the same code as the pages** — every API route resolves through
`process_view` too, so there is no second mechanism to keep in step.

## The rule

    unauthenticated ......................... proceed (login is not a paywall)
    free entitlement, or app not in the
      catalogue at all ...................... proceed
    a view marked @plan_exempt .............. proceed (a brake, see below)
    the user's plan grants it ............... proceed
    safe method (GET/HEAD/OPTIONS) .......... proceed, and mark the request
    anything else ........................... 402

Refusing writes while allowing reads is what "read-only viewing of paid apps"
means in code. It has a property worth stating: **a lapsed subscriber can always
get their data out.** Downloads are GETs. Nothing anybody made is ever behind
the paywall, which is what makes lapsing a safe thing to let happen
automatically.

## Brakes

Some writes must work without the plan: the ones that STOP a paid app doing
something it keeps doing on its own. A scheduled job that runs and bills
whatever the plan says cannot put its Pause behind the plan, or a lapse turns
into a bill nobody can halt. ``@plan_exempt`` marks such a view, per view like
``csrf_exempt``, so the app stays paid and every other write in it still 402s.
It is for brakes and for letting them off again: a marked view that makes the
app do anything it was not already set to do is that feature given away.

## Failure

`plan_for` is wrapped: a database mid-migrate must not 402 the whole site. But
the fallback is asymmetric on purpose — an unresolvable plan proceeds for safe
methods and **refuses writes**. Delta's helper does `except Exception: return
False`, which grants access on a DB fault; that is the wrong way round for
anything that decides what somebody has paid for.
"""

from __future__ import annotations

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _

#: App names that are never gated, whatever the catalogue says. Login, the
#: wallet and the plans page have to work for somebody with no plan and no
#: money, or there is no way to ever get one. `admin` is here because Django's
#: own admin is staff-only already and 402-ing it would be absurd.
ALWAYS_FREE = frozenset({
    "admin", "core", "sso", "sso_client", "sso_core", "api", "subscriptions",
    "assets", "quota", "tariffs", "socialhub", "people", "gervazy",
    # Your own pools: a member with no plan must still see why they cannot act.
    "mana",
})

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

PAYMENT_REQUIRED = 402


def plan_exempt(view_func):
    """Mark a view the gate never refuses: a brake (see the module docstring).

    The gate reads the mark off the view the URL resolves to, so it goes on
    that function, outermost among its decorators or anywhere inside ones
    that copy attributes (``functools.wraps`` does).
    """
    view_func.plan_exempt = True
    return view_func


def entitlement_for_request(request) -> str:
    """The entitlement code this request is asking for, or "" for none."""
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    return match.app_name or ""


def is_entitled(user, feature_key: str) -> bool:
    """Whether this user's current plan grants this feature.

    Free features and unknown apps answer True — an app nobody declared is
    not a thing somebody forgot to pay for, it is a thing nobody decided to
    sell, and refusing it would make installing a new app a silent outage.

    There is no longer a "nothing seeded, so nothing is gated" branch. It
    existed because plans were rows and a fresh host had none, which made
    SEEDING the thing that turned gating on — a surprising place for that
    decision to live. The ladder is a validated file now and is never empty,
    so enforcement is decided by BUILD_SUBSCRIPTIONS_ENFORCE alone, which is
    what the flag always claimed to do.
    """
    from .catalogue import registry

    if not feature_key or feature_key in ALWAYS_FREE:
        return True
    entitlement = registry.get(feature_key)
    if entitlement is None or entitlement.free:
        return True

    from .models import plan_for

    return plan_for(user).grants(feature_key)


def _wants_json(request) -> bool:
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = request.headers.get("Accept", "")
    if "application/json" in accept:
        return True
    return request.content_type == "application/json"


def superuser_plan_required(view):
    """A view for superuser functionality: a real superuser on the admin-only
    plan (both, `models.superuser_plan_active`), 403 for anybody else —
    including a superuser whose Community has not granted them the plan."""
    from functools import wraps

    from django.core.exceptions import PermissionDenied

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        from .models import superuser_plan_active

        if not superuser_plan_active(getattr(request, "user", None)):
            raise PermissionDenied("This needs a superuser on the Superuser plan.")
        return view(request, *args, **kwargs)

    wrapped.superuser_plan_required = True
    return wrapped


class SubscriptionGateMiddleware:
    """Read-only without the plan, 402 on a write."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        from django.conf import settings

        request.plan_locked = False
        # Installed but switched off: a host may turn enforcement off for a
        # test run while keeping it obligatory everywhere else (1.51).
        if not getattr(settings, "SUBSCRIPTION_ENFORCEMENT", True):
            return None

        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return None

        code = entitlement_for_request(request)
        if not code or code in ALWAYS_FREE or getattr(view_func, "plan_exempt", False):
            return None

        try:
            entitled = is_entitled(user, code)
        except Exception:  # noqa: BLE001
            # See the module docstring: open for reads, closed for writes.
            entitled = request.method in SAFE_METHODS

        if entitled:
            return None

        if request.method in SAFE_METHODS and not getattr(settings, "SUBSCRIPTION_GATE_READS", False):
            # Visible, and inert. The template can say so; nothing here does.
            request.plan_locked = True
            return None
        # SUBSCRIPTION_GATE_READS (1.51): an app outside the plan is not opened
        # at all — for a host whose plans sell access, not only writing.

        return self.refuse(request, code)

    # -- refusal ------------------------------------------------------------

    def refuse(self, request, code: str) -> HttpResponse:
        from .catalogue import registry

        entitlement = registry.get(code)
        label = entitlement.label if entitlement else code
        message = _("%(feature)s is not included in your plan.") % {"feature": label}

        if _wants_json(request):
            return JsonResponse(
                {"error": message, "reason": "subscription-required",
                 "entitlement": code, "plans_url": self._plans_url()},
                status=PAYMENT_REQUIRED,
            )
        context = {
            "message": message,
            "entitlement": entitlement,
            "plans_url": self._plans_url(),
        }
        return render(request, "subscriptions/locked.html",
                      self._decorate(context, request),
                      status=PAYMENT_REQUIRED)

    @staticmethod
    def _decorate(context: dict, request) -> dict:
        """The platform's page context, and never at the cost of the refusal.

        ``locked.html`` extends ``oya/base.html``, whose palette comes from the
        Platform record rather than a stylesheet — so rendering this bare gave
        an unstyled 402 on the one page whose job is to persuade somebody to
        subscribe. Imported here rather than reusing ``views._render`` because
        this is middleware and that module imports back into this one.

        **The fallback is the point.** ``PageProcessor()`` raises ``Http404``
        when no Platform row is active, which in middleware would turn a 402
        refusal into a 404 — a host mid-setup would answer "no such page" to
        every gated write. An ugly refusal beats a wrong one, so a missing
        platform costs the styling and nothing else.
        """
        from django.http import Http404

        from toto.ui import PageProcessor

        try:
            return PageProcessor().decorate(context, request)
        except Http404:
            return context

    @staticmethod
    def _plans_url() -> str:
        from django.urls import NoReverseMatch, reverse

        try:
            return reverse("subscriptions:plans")
        except NoReverseMatch:
            return ""


def subscription_context(request):
    """`plan`, `plan_locked` and `subscription` for every template.

    Registered as a context processor. Everything degrades to None so a page
    renders identically on a host with no subscriptions app — the same contract
    every façade in this tree keeps.
    """
    payload = {"my_plan": None, "my_subscription": None,
               "plan_locked": bool(getattr(request, "plan_locked", False))}
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return payload
    try:
        from .models import Subscription, plan_for

        payload["my_plan"] = plan_for(user)
        payload["my_subscription"] = (Subscription.objects
                                      .filter(user=user)
                                      .first())
    except Exception:  # noqa: BLE001 - a context processor runs on every page
        return payload
    return payload
