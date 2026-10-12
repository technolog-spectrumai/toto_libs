"""The community sets the ceiling; the person chooses within it (2026-10-11).

``settings.SUBSCRIPTION_AUTO_ASSIGN`` (off unless a host turns it on) makes a
plan follow community membership: a person with no subscription of their
own is put on the best plan their communities offer, and one whose plan is
no longer offered to them (they left the community, the offer was withdrawn)
is put on the best of what is left, the default plan at least. A plan the
person chose for themselves on the plans page is theirs for as long as a
community of theirs offers it: the communities decide what is available,
never which of it is held. The billing sweep does the same correction
nightly, for whatever a signal missed.

What is never touched: a superuser (``bootstrap_plans`` puts every superuser
on the plan for admins, which ranks above everything), a subscription an
operator ``forced`` on (``services.subscribe(force=True)``), and a plan that
costs units on a host with a rate card, which is a purchase and not a role.
Staff are members like any other here: the carve-out that lets them see
every plan on the plans page does not put them on one. The "best" is the eligible plan with the highest ``order`` (the ladder's
own ranking, low first on the page), the default plan when nothing else is
offered. The whole weight rests on ``services.is_eligible``, the one
predicate the purchase itself passes.
"""
from __future__ import annotations

import logging

from django.conf import settings

log = logging.getLogger("toto.subscriptions")


def enabled() -> bool:
    return bool(getattr(settings, "SUBSCRIPTION_AUTO_ASSIGN", False))


def best_plan_for(user):
    """The highest-ranked plan this person may hold, the default plan at
    least; ``None`` for somebody not signed in. Plans for admins are left to
    ``bootstrap_plans``; a plan that costs units is not assigned unasked."""
    from . import plans, services

    if user is None or not getattr(user, "is_authenticated", False):
        return None
    # What the person's communities offer, and nothing else: the carve-out
    # that lets staff SEE every plan (they administer the offers) must not
    # put every staff account on the top plan unasked.
    offered = services._offered_keys(user)
    candidates = [plan for plan in plans.all_plans()
                  if not plan.admin_only and (plan.is_default or plan.key in offered)
                  and (plan.is_default or plan.units == 0)]
    if not candidates:
        return plans.default_plan()
    return max(candidates, key=lambda plan: (plan.order, plan.is_default))


def assign_best(user) -> bool:
    """Put ``user`` on their best plan when they hold none of their own, or
    on the best still offered when the one they hold no longer is. True when
    the subscription changed. A plan the person chose and may still hold, a
    superuser on the plan for admins and a forced row are left as they are."""
    from .models import Subscription, plan_for
    from .services import is_eligible, subscribe

    if getattr(user, "is_superuser", False):
        return False                    # bootstrap_plans puts superusers on the plan for admins
    best = best_plan_for(user)
    if best is None:
        return False
    held = Subscription.objects.filter(user=user).first()
    if held is not None and held.forced:
        return False
    current = plan_for(user)
    if current.admin_only:
        return False
    if held is not None and held.is_paying and not held.is_expired():
        # Their own choice: kept while a community of theirs still offers it
        # (the default plan always is).
        chosen = held.plan
        if chosen is not None and (chosen.is_default or is_eligible(user, chosen.key)):
            return False
    if current.key == best.key and held is None and best.is_default:
        return False                    # the default plan needs no row
    if held is not None and held.plan_key == best.key and held.is_paying and not held.is_expired():
        return False
    subscribe(user, best)
    log.info("subscriptions: %s put on %s (automatic)", user.get_username(), best.key)
    return True


def assign_all() -> int:
    """Every account, once: what the ingress and the billing sweep call.
    Returns how many subscriptions changed."""
    from django.contrib.auth import get_user_model

    changed = 0
    for user in (get_user_model().objects.filter(is_active=True, is_superuser=False)
                 .order_by("pk").iterator()):
        try:
            changed += int(assign_best(user))
        except Exception:  # noqa: BLE001 - one account must not stop the rest
            log.exception("subscriptions: automatic assignment failed for %s", user.pk)
    return changed


def _membership_changed(sender, instance, action, reverse, model, pk_set, **kwargs):
    """A person joined or left a community (``Person.communities``, from
    either side): their best plan may have changed."""
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    # Asked now, not only when the receiver was connected: a host that turns
    # assignment off (a test's override too) is not assigned by a receiver
    # an earlier setting connected.
    if not enabled():
        return
    from toto.people.models import Person

    if reverse:
        # The community side: instance is a Community; the people are pk_set
        # (post_clear carries none: every member of it, then).
        people = (Person.objects.filter(pk__in=pk_set) if pk_set
                  else Person.objects.filter(communities=instance))
    else:
        people = [instance]
    for person in people:
        if person.user_id:
            try:
                assign_best(person.user)
            except Exception:  # noqa: BLE001 - never break a membership change
                log.exception("subscriptions: automatic assignment failed for %s", person.pk)


def connect() -> None:
    """Listen to membership changes, where the host turned assignment on."""
    if not enabled():
        return
    from django.db.models.signals import m2m_changed

    from toto.people.models import Person

    m2m_changed.connect(_membership_changed, sender=Person.communities.through, weak=False,
                        dispatch_uid="toto.subscriptions.autoplan")
