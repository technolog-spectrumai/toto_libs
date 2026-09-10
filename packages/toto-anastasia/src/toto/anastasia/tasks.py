"""The beat entry that keeps bookings and reality in step.

One task, not three, and one beat entry — the same judgement
``toto.lifecycle.tasks.evaluate`` makes: expiring a lease, telling the manager
which Capsules still exist, and noticing that a Capsule's runtime has died are three
views of one question, and running them as separate entries lets them race
(a lease expiring while reconciliation is mid-pass would have the manager
destroy a Capsule the database still calls live, or the reverse).

**The caller's list is authoritative.** The manager does not know what a lease
is; it holds containers and cgroups labelled with opaque ids. So this task
sends the set of Capsules that SHOULD exist, and the manager destroys whatever
else it is holding. That direction is the whole reconciliation contract.
"""

from __future__ import annotations

import logging

from celery import shared_task

log = logging.getLogger("toto.anastasia.tasks")


@shared_task(name="toto.anastasia.tasks.reconcile", ignore_result=True,
             soft_time_limit=240, time_limit=300)
def reconcile() -> dict:
    """Expire what is due, then make the manager agree with what is left."""
    from django.apps import apps

    if not apps.is_installed("toto.anastasia"):
        # A beat entry can outlive the app that wanted it — the trap
        # toto.registry documents four separate times.
        return {"skipped": "not installed"}

    from . import samples, services
    from .models import ComputeLease
    from .runtime import get_backend

    expired = services.expire_due()

    # Read AFTER expiring, so a lease that just lapsed is not sent as live and
    # then destroyed on the next pass.
    known = list(ComputeLease.objects.open().values_list("uuid", flat=True))

    result = {"expired": expired, "known_capsules": len(known)}
    backend = get_backend()

    # Two INDEPENDENT jobs, and keeping them independent is load-bearing.
    #
    # Telling the manager which Capsules exist needs a manager; noticing that a
    # Capsule's runtime has died does not. An earlier version returned early when
    # the backend had no reconcile() and so never refreshed at all — which left
    # every Capsule reporting whatever it last reported, forever, on exactly the
    # backends where that matters most.
    reconcile_with_manager = getattr(backend, "reconcile", None)
    if reconcile_with_manager is None:
        # The null backend has nothing to reconcile with, and that is not a
        # failure: booking is arithmetic that works with no manager at all.
        result["manager"] = {"configured": False}
    else:
        try:
            result["manager"] = reconcile_with_manager(known)
        except Exception:  # noqa: BLE001 - a beat task must not die on a silent peer
            log.warning("anastasia: the manager did not answer reconciliation")
            result["manager"] = {"unreachable": True}

    # Fold each mounted Capsule's live sample onto its row, so the desk shows
    # something current even for a user who has not opened the page — and so a
    # manager that restarted is noticed without anyone having to look.
    refreshed = recorded = 0
    for lease in ComputeLease.objects.open().select_related("runtime"):
        runtime = services.runtime_for(lease)
        if not runtime.is_mounted:
            continue
        services.refresh_runtime(lease)
        refreshed += 1
        # THE HISTORY TABLE IS WRITTEN HERE AND NOWHERE ELSE. `CapsuleSample`
        # shipped with a recorder and a pruner and no caller for either, so
        # every chart drawn from it was empty and every row it would have
        # kept was never written. The throttle is asked FIRST, because the
        # storage reading is a tree walk on the executor and this tick runs
        # every two minutes; a walk whose result is thrown away is the one
        # cost here worth avoiding.
        if not samples.due(lease):
            continue
        storage = None
        read_storage = getattr(backend, "storage", None)
        if read_storage is not None:
            try:
                storage = read_storage(lease) or None
            except Exception:  # noqa: BLE001 — a reading is information, not a precondition
                log.warning("anastasia: no storage reading for %s", lease.uuid)
        if samples.record(lease, services.capsule_report(lease), storage):
            recorded += 1
    result["refreshed"] = refreshed
    result["recorded"] = recorded
    # Retention is this tick's job too. If this task is ever removed, the
    # table has to go with it — `samples.py` says so in its header.
    result["pruned"] = samples.prune()
    return result
