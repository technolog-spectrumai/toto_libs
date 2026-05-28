from __future__ import annotations

import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from toto.assets.models import Obligation, ObligationStatus
from toto.ui import PageProcessor

from .models import (
    Allocation,
    AllocationStatus,
    Condition,
    ConditionStatus,
    ContractEvent,
    ContractEventKind,
    Entitlement,
    EntitlementStatus,
    Schedule,
    ScheduleStatus,
)


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

def overview(request):
    now = timezone.now()
    stats = {
        "entitlements_total": Entitlement.objects.count(),
        "entitlements_active": Entitlement.objects.filter(status=EntitlementStatus.ACTIVE).count(),
        "entitlements_revoked": Entitlement.objects.filter(status=EntitlementStatus.REVOKED).count(),
        "obligations_total": Obligation.objects.count(),
        "obligations_pending": Obligation.objects.filter(status=ObligationStatus.PENDING).count(),
        "obligations_overdue": Obligation.objects.filter(
            status=ObligationStatus.PENDING, due_at__lt=now
        ).count(),
        "schedules_total": Schedule.objects.count(),
        "schedules_active": Schedule.objects.filter(status=ScheduleStatus.ACTIVE).count(),
        "schedules_draft": Schedule.objects.filter(status=ScheduleStatus.DRAFT).count(),
        "schedules_due": Schedule.objects.due(now=now).count(),
        "conditions_total": Condition.objects.count(),
        "conditions_pending": Condition.objects.filter(status=ConditionStatus.PENDING).count(),
        "conditions_satisfied": Condition.objects.filter(status=ConditionStatus.SATISFIED).count(),
        "conditions_failed": Condition.objects.filter(status=ConditionStatus.FAILED).count(),
        "allocations_total": Allocation.objects.count(),
        "allocations_active": Allocation.objects.filter(status=AllocationStatus.ACTIVE).count(),
        "allocations_draft": Allocation.objects.filter(status=AllocationStatus.DRAFT).count(),
        "events_total": ContractEvent.objects.count(),
    }
    recent_events = ContractEvent.objects.select_related(
        "agreement", "contract"
    ).order_by("-created_at")[:8]
    return _render(request, "claims/overview.html", {
        "stats": stats,
        "recent_events": recent_events,
    })


# ---------------------------------------------------------------------------
# Entitlement
# ---------------------------------------------------------------------------

def entitlement_list(request):
    qs = Entitlement.objects.select_related("holder_account", "agreement", "contract")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    kind = request.GET.get("kind", "").strip()
    if q:
        qs = qs.filter(
            Q(resource_label__icontains=q)
            | Q(source_type__icontains=q)
            | Q(source_id__icontains=q)
            | Q(holder_account__code__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    if kind:
        qs = qs.filter(kind=kind)
    from .models import EntitlementKind
    return _render(request, "claims/entitlement_list.html", {
        "entitlements": qs.order_by("-created_at"),
        "q": q,
        "status_filter": status,
        "kind_filter": kind,
        "status_choices": EntitlementStatus.choices,
        "kind_choices": EntitlementKind.choices,
        "total": qs.count(),
    })


def entitlement_detail(request, uuid):
    from django.urls import reverse
    entitlement = get_object_or_404(
        Entitlement.objects.select_related("holder_account", "agreement", "contract"),
        uuid=uuid,
    )
    related_events = ContractEvent.objects.filter(entitlement=entitlement).order_by("-created_at")[:10]
    s = entitlement.status
    actions = []
    if s == EntitlementStatus.ACTIVE:
        actions = [
            {"action": "suspend", "label": "Suspend",    "color": "yellow", "icon": "fa-solid fa-pause",       "confirm": "Suspend this entitlement?"},
            {"action": "revoke",  "label": "Revoke",     "color": "red",    "icon": "fa-solid fa-ban",          "needs_reason": True, "confirm": "Revoke this entitlement?"},
        ]
    elif s == EntitlementStatus.SUSPENDED:
        actions = [
            {"action": "reactivate", "label": "Reactivate", "color": "green", "icon": "fa-solid fa-play",      "confirm": "Reactivate this entitlement?"},
            {"action": "revoke",     "label": "Revoke",     "color": "red",   "icon": "fa-solid fa-ban",        "needs_reason": True, "confirm": "Revoke this entitlement?"},
        ]
    return _render(request, "claims/entitlement_detail.html", {
        "entitlement": entitlement,
        "related_events": related_events,
        "actions": actions,
        "transition_url": reverse("claims:entitlement_transition", args=[uuid]),
    })


# ---------------------------------------------------------------------------
# Obligation
# ---------------------------------------------------------------------------

def obligation_list(request):
    qs = Obligation.objects.select_related(
        "debtor_account", "creditor_account", "asset",
        "collateral_account", "collateral_asset",
    )
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        qs = qs.filter(
            Q(reference__icontains=q)
            | Q(debtor_account__code__icontains=q)
            | Q(creditor_account__code__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    now = timezone.now()

    my_account_pks = set()
    if request.user.is_authenticated:
        from toto.assets.models import LedgerAccount
        my_account_pks = set(
            LedgerAccount.objects.filter(user=request.user, active=True).values_list("pk", flat=True)
        )

    return _render(request, "claims/obligation_list.html", {
        "obligations": qs.order_by("due_at"),
        "q": q,
        "status_filter": status,
        "status_choices": ObligationStatus.choices,
        "total": qs.count(),
        "now": now,
        "my_account_pks": my_account_pks,
    })


def obligation_detail(request, pk):
    obligation = get_object_or_404(
        Obligation.objects.select_related(
            "debtor_account", "creditor_account", "asset",
            "collateral_account", "collateral_asset",
        ),
        pk=pk,
    )
    related_events = ContractEvent.objects.filter(obligation=obligation).order_by("-created_at")[:10]

    is_my_obligation = (
        request.user.is_authenticated
        and obligation.debtor_account.user_id == request.user.id
    )

    return _render(request, "claims/obligation_detail.html", {
        "obligation": obligation,
        "related_events": related_events,
        "now": timezone.now(),
        "is_my_obligation": is_my_obligation,
    })


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

def schedule_list(request):
    qs = Schedule.objects.select_related("agreement", "contract")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    kind = request.GET.get("kind", "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(source_type__icontains=q)
            | Q(source_id__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    if kind:
        qs = qs.filter(kind=kind)
    from .models import ScheduleKind
    return _render(request, "claims/schedule_list.html", {
        "schedules": qs.order_by("next_run_at", "starts_at"),
        "q": q,
        "status_filter": status,
        "kind_filter": kind,
        "status_choices": ScheduleStatus.choices,
        "kind_choices": ScheduleKind.choices,
        "total": qs.count(),
    })


def schedule_detail(request, uuid):
    from django.urls import reverse
    schedule = get_object_or_404(
        Schedule.objects.select_related("agreement", "contract"),
        uuid=uuid,
    )
    related_events = ContractEvent.objects.filter(schedule=schedule).order_by("-created_at")[:10]
    s = schedule.status
    actions = []
    if s == ScheduleStatus.DRAFT:
        actions = [
            {"action": "activate", "label": "Activate", "color": "green", "icon": "fa-solid fa-play",    "confirm": "Activate this schedule?"},
            {"action": "cancel",   "label": "Cancel",   "color": "red",   "icon": "fa-solid fa-xmark",   "confirm": "Cancel this schedule?"},
        ]
    elif s == ScheduleStatus.ACTIVE:
        actions = [
            {"action": "pause",    "label": "Pause",    "color": "yellow", "icon": "fa-solid fa-pause",  "confirm": "Pause this schedule?"},
            {"action": "complete", "label": "Complete", "color": "blue",   "icon": "fa-solid fa-check",  "confirm": "Mark this schedule as completed?"},
            {"action": "cancel",   "label": "Cancel",   "color": "red",    "icon": "fa-solid fa-xmark",  "confirm": "Cancel this schedule?"},
        ]
    elif s == ScheduleStatus.PAUSED:
        actions = [
            {"action": "resume",  "label": "Resume",   "color": "green", "icon": "fa-solid fa-play",    "confirm": "Resume this schedule?"},
            {"action": "cancel",  "label": "Cancel",   "color": "red",   "icon": "fa-solid fa-xmark",   "confirm": "Cancel this schedule?"},
        ]
    return _render(request, "claims/schedule_detail.html", {
        "schedule": schedule,
        "related_events": related_events,
        "actions": actions,
        "transition_url": reverse("claims:schedule_transition", args=[uuid]),
    })


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------

def condition_list(request):
    qs = Condition.objects.select_related("agreement", "contract")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    kind = request.GET.get("kind", "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(description__icontains=q)
            | Q(source_type__icontains=q)
            | Q(source_id__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    if kind:
        qs = qs.filter(kind=kind)
    from .models import ConditionKind
    return _render(request, "claims/condition_list.html", {
        "conditions": qs.order_by("-created_at"),
        "q": q,
        "status_filter": status,
        "kind_filter": kind,
        "status_choices": ConditionStatus.choices,
        "kind_choices": ConditionKind.choices,
        "total": qs.count(),
    })


def condition_detail(request, uuid):
    from django.urls import reverse
    condition = get_object_or_404(
        Condition.objects.select_related("agreement", "contract"),
        uuid=uuid,
    )
    related_events = ContractEvent.objects.filter(condition=condition).order_by("-created_at")[:10]
    actions = []
    if condition.status == ConditionStatus.PENDING:
        actions = [
            {"action": "satisfy", "label": "Satisfy",  "color": "green",  "icon": "fa-solid fa-check",        "confirm": "Mark this condition as satisfied?"},
            {"action": "fail",    "label": "Fail",      "color": "red",    "icon": "fa-solid fa-xmark",        "needs_reason": True, "confirm": "Mark this condition as failed?"},
            {"action": "waive",   "label": "Waive",     "color": "yellow", "icon": "fa-solid fa-hand",         "needs_reason": True, "confirm": "Waive this condition?"},
            {"action": "cancel",  "label": "Cancel",    "color": "gray",   "icon": "fa-solid fa-ban",          "confirm": "Cancel this condition?"},
        ]
    return _render(request, "claims/condition_detail.html", {
        "condition": condition,
        "related_events": related_events,
        "expression_json": json.dumps(condition.expression, indent=2) if condition.expression else "",
        "actions": actions,
        "transition_url": reverse("claims:condition_transition", args=[uuid]),
    })


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------

def allocation_list(request):
    qs = Allocation.objects.select_related(
        "asset", "holder_account", "beneficiary_account", "agreement", "contract"
    )
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    kind = request.GET.get("kind", "").strip()
    if q:
        qs = qs.filter(
            Q(source_type__icontains=q)
            | Q(source_id__icontains=q)
            | Q(asset__unit_name__icontains=q)
            | Q(holder_account__code__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    if kind:
        qs = qs.filter(kind=kind)
    from .models import AllocationKind
    return _render(request, "claims/allocation_list.html", {
        "allocations": qs.order_by("-created_at"),
        "q": q,
        "status_filter": status,
        "kind_filter": kind,
        "status_choices": AllocationStatus.choices,
        "kind_choices": AllocationKind.choices,
        "total": qs.count(),
    })


def allocation_detail(request, uuid):
    from django.urls import reverse
    allocation = get_object_or_404(
        Allocation.objects.select_related(
            "asset", "holder_account", "beneficiary_account", "agreement", "contract"
        ),
        uuid=uuid,
    )
    related_events = ContractEvent.objects.filter(allocation=allocation).order_by("-created_at")[:10]
    s = allocation.status
    actions = []
    if s == AllocationStatus.DRAFT:
        actions = [
            {"action": "activate", "label": "Activate", "color": "green", "icon": "fa-solid fa-play",   "confirm": "Activate this allocation?"},
            {"action": "cancel",   "label": "Cancel",   "color": "red",   "icon": "fa-solid fa-ban",    "confirm": "Cancel this allocation?"},
        ]
    elif s == AllocationStatus.ACTIVE:
        actions = [
            {"action": "release_all", "label": "Release All", "color": "blue",   "icon": "fa-solid fa-unlock",       "confirm": "Release the full remaining allocation?"},
            {"action": "consume_all", "label": "Consume All", "color": "yellow", "icon": "fa-solid fa-fire",         "confirm": "Consume the full remaining allocation?"},
            {"action": "cancel",      "label": "Cancel",      "color": "red",    "icon": "fa-solid fa-ban",          "confirm": "Cancel this allocation?"},
        ]
    return _render(request, "claims/allocation_detail.html", {
        "allocation": allocation,
        "related_events": related_events,
        "actions": actions,
        "transition_url": reverse("claims:allocation_transition", args=[uuid]),
    })


# ---------------------------------------------------------------------------
# ContractEvent
# ---------------------------------------------------------------------------

def event_list(request):
    qs = ContractEvent.objects.select_related(
        "agreement", "contract", "obligation", "entitlement",
        "schedule", "condition", "allocation",
    )
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "").strip()
    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(source_type__icontains=q)
            | Q(source_id__icontains=q)
            | Q(description__icontains=q)
        )
    if kind:
        qs = qs.filter(kind=kind)
    return _render(request, "claims/event_list.html", {
        "events": qs.order_by("-created_at"),
        "q": q,
        "kind_filter": kind,
        "kind_choices": ContractEventKind.choices,
        "total": qs.count(),
    })


def event_detail(request, uuid):
    event = get_object_or_404(
        ContractEvent.objects.select_related(
            "agreement", "contract", "actor", "transaction",
            "obligation", "entitlement", "schedule", "condition", "allocation",
        ),
        uuid=uuid,
    )
    return _render(request, "claims/event_detail.html", {
        "event": event,
        "payload_json": json.dumps(event.payload, indent=2) if event.payload else "",
    })


# ---------------------------------------------------------------------------
# Lifecycle graph JSON
# ---------------------------------------------------------------------------

def _node(id, label, kind, url="", status=""):
    return {"data": {"id": id, "label": label, "kind": kind, "url": url, "status": status}}


def _edge(source, target, label=""):
    return {"data": {"id": f"{source}__{target}", "source": source, "target": target, "label": label}}


def lifecycle_graph_json(request):
    model = request.GET.get("model", "")
    obj_id = request.GET.get("id", "")
    include_accounts = request.GET.get("include_accounts", "1") != "0"
    include_events = request.GET.get("include_events", "1") != "0"

    nodes = []
    edges = []

    try:
        if model == "entitlement":
            obj = Entitlement.objects.select_related(
                "holder_account", "agreement", "contract"
            ).get(uuid=obj_id)
            nodes.append(_node(f"e_{obj.pk}", obj.resource_label, "entitlement",
                               f"/claims/entitlements/{obj.uuid}/", obj.status))
            if include_accounts:
                acc = obj.holder_account
                nodes.append(_node(f"acc_{acc.pk}", acc.code, "account", f"/assets/accounts/{acc.pk}/"))
                edges.append(_edge(f"e_{obj.pk}", f"acc_{acc.pk}", "holder"))
            if obj.agreement:
                nodes.append(_node(f"agr_{obj.agreement.pk}", f"Agreement {obj.agreement.uuid}", "agreement"))
                edges.append(_edge(f"agr_{obj.agreement.pk}", f"e_{obj.pk}", "governs"))
            if obj.contract:
                nodes.append(_node(f"con_{obj.contract.pk}", obj.contract.name, "contract",
                                   f"/assets/contracts/{obj.contract.uuid}/"))
                edges.append(_edge(f"con_{obj.contract.pk}", f"e_{obj.pk}", "governs"))
            if include_events:
                for ev in ContractEvent.objects.filter(entitlement=obj).order_by("-created_at")[:5]:
                    nodes.append(_node(f"ev_{ev.pk}", ev.title, "event",
                                       f"/claims/events/{ev.uuid}/", ev.kind))
                    edges.append(_edge(f"e_{obj.pk}", f"ev_{ev.pk}", ev.get_kind_display()))

        elif model == "obligation":
            obj = Obligation.objects.select_related(
                "debtor_account", "creditor_account", "asset"
            ).get(pk=obj_id)
            nodes.append(_node(f"ob_{obj.pk}", obj.reference, "obligation",
                               f"/claims/obligations/{obj.pk}/", obj.status))
            if include_accounts:
                nodes.append(_node(f"acc_d_{obj.debtor_account.pk}", obj.debtor_account.code, "account"))
                nodes.append(_node(f"acc_c_{obj.creditor_account.pk}", obj.creditor_account.code, "account"))
                edges.append(_edge(f"acc_d_{obj.debtor_account.pk}", f"ob_{obj.pk}", "debtor"))
                edges.append(_edge(f"ob_{obj.pk}", f"acc_c_{obj.creditor_account.pk}", "creditor"))
            nodes.append(_node(f"asset_{obj.asset.pk}", obj.asset.unit_name, "asset"))
            edges.append(_edge(f"ob_{obj.pk}", f"asset_{obj.asset.pk}", "asset"))
            if include_events:
                for ev in ContractEvent.objects.filter(obligation=obj).order_by("-created_at")[:5]:
                    nodes.append(_node(f"ev_{ev.pk}", ev.title, "event",
                                       f"/claims/events/{ev.uuid}/", ev.kind))
                    edges.append(_edge(f"ob_{obj.pk}", f"ev_{ev.pk}", ev.get_kind_display()))

        elif model == "schedule":
            obj = Schedule.objects.select_related("agreement", "contract").get(uuid=obj_id)
            nodes.append(_node(f"s_{obj.pk}", obj.name, "schedule",
                               f"/claims/schedules/{obj.uuid}/", obj.status))
            if obj.agreement:
                nodes.append(_node(f"agr_{obj.agreement.pk}", f"Agreement {obj.agreement.uuid}", "agreement"))
                edges.append(_edge(f"agr_{obj.agreement.pk}", f"s_{obj.pk}", "governs"))
            if obj.contract:
                nodes.append(_node(f"con_{obj.contract.pk}", obj.contract.name, "contract",
                                   f"/assets/contracts/{obj.contract.uuid}/"))
                edges.append(_edge(f"con_{obj.contract.pk}", f"s_{obj.pk}", "governs"))
            if include_events:
                for ev in ContractEvent.objects.filter(schedule=obj).order_by("-created_at")[:5]:
                    nodes.append(_node(f"ev_{ev.pk}", ev.title, "event",
                                       f"/claims/events/{ev.uuid}/", ev.kind))
                    edges.append(_edge(f"s_{obj.pk}", f"ev_{ev.pk}", ev.get_kind_display()))

        elif model == "condition":
            obj = Condition.objects.select_related("agreement", "contract").get(uuid=obj_id)
            nodes.append(_node(f"c_{obj.pk}", obj.name, "condition",
                               f"/claims/conditions/{obj.uuid}/", obj.status))
            if obj.agreement:
                nodes.append(_node(f"agr_{obj.agreement.pk}", f"Agreement {obj.agreement.uuid}", "agreement"))
                edges.append(_edge(f"agr_{obj.agreement.pk}", f"c_{obj.pk}", "governs"))
            if obj.contract:
                nodes.append(_node(f"con_{obj.contract.pk}", obj.contract.name, "contract",
                                   f"/assets/contracts/{obj.contract.uuid}/"))
                edges.append(_edge(f"con_{obj.contract.pk}", f"c_{obj.pk}", "governs"))
            if include_events:
                for ev in ContractEvent.objects.filter(condition=obj).order_by("-created_at")[:5]:
                    nodes.append(_node(f"ev_{ev.pk}", ev.title, "event",
                                       f"/claims/events/{ev.uuid}/", ev.kind))
                    edges.append(_edge(f"c_{obj.pk}", f"ev_{ev.pk}", ev.get_kind_display()))

        elif model == "allocation":
            obj = Allocation.objects.select_related(
                "asset", "holder_account", "beneficiary_account",
                "agreement", "contract"
            ).get(uuid=obj_id)
            nodes.append(_node(f"al_{obj.pk}", obj.get_kind_display(), "allocation",
                               f"/claims/allocations/{obj.uuid}/", obj.status))
            nodes.append(_node(f"asset_{obj.asset.pk}", obj.asset.unit_name, "asset"))
            edges.append(_edge(f"al_{obj.pk}", f"asset_{obj.asset.pk}", "asset"))
            if include_accounts:
                if obj.holder_account:
                    nodes.append(_node(f"acc_h_{obj.holder_account.pk}", obj.holder_account.code, "account"))
                    edges.append(_edge(f"acc_h_{obj.holder_account.pk}", f"al_{obj.pk}", "holder"))
                if obj.beneficiary_account:
                    nodes.append(_node(f"acc_b_{obj.beneficiary_account.pk}",
                                       obj.beneficiary_account.code, "account"))
                    edges.append(_edge(f"al_{obj.pk}", f"acc_b_{obj.beneficiary_account.pk}", "beneficiary"))
            if include_events:
                for ev in ContractEvent.objects.filter(allocation=obj).order_by("-created_at")[:5]:
                    nodes.append(_node(f"ev_{ev.pk}", ev.title, "event",
                                       f"/claims/events/{ev.uuid}/", ev.kind))
                    edges.append(_edge(f"al_{obj.pk}", f"ev_{ev.pk}", ev.get_kind_display()))

        elif model == "event":
            obj = ContractEvent.objects.select_related(
                "agreement", "contract", "transaction",
                "obligation", "entitlement", "schedule", "condition", "allocation",
            ).get(uuid=obj_id)
            nodes.append(_node(f"ev_{obj.pk}", obj.title, "event",
                               f"/claims/events/{obj.uuid}/", obj.kind))
            if obj.entitlement:
                en = obj.entitlement
                nodes.append(_node(f"e_{en.pk}", en.resource_label, "entitlement",
                                   f"/claims/entitlements/{en.uuid}/", en.status))
                edges.append(_edge(f"ev_{obj.pk}", f"e_{en.pk}", "entitlement"))
            if obj.schedule:
                sc = obj.schedule
                nodes.append(_node(f"s_{sc.pk}", sc.name, "schedule",
                                   f"/claims/schedules/{sc.uuid}/", sc.status))
                edges.append(_edge(f"ev_{obj.pk}", f"s_{sc.pk}", "schedule"))
            if obj.condition:
                co = obj.condition
                nodes.append(_node(f"c_{co.pk}", co.name, "condition",
                                   f"/claims/conditions/{co.uuid}/", co.status))
                edges.append(_edge(f"ev_{obj.pk}", f"c_{co.pk}", "condition"))
            if obj.allocation:
                al = obj.allocation
                nodes.append(_node(f"al_{al.pk}", al.get_kind_display(), "allocation",
                                   f"/claims/allocations/{al.uuid}/", al.status))
                edges.append(_edge(f"ev_{obj.pk}", f"al_{al.pk}", "allocation"))
            if obj.obligation:
                ob = obj.obligation
                nodes.append(_node(f"ob_{ob.pk}", ob.reference, "obligation",
                                   f"/claims/obligations/{ob.pk}/", ob.status))
                edges.append(_edge(f"ev_{obj.pk}", f"ob_{ob.pk}", "obligation"))
            if obj.agreement:
                nodes.append(_node(f"agr_{obj.agreement.pk}", f"Agreement {obj.agreement.uuid}", "agreement"))
                edges.append(_edge(f"agr_{obj.agreement.pk}", f"ev_{obj.pk}", "agreement"))
            if obj.contract:
                nodes.append(_node(f"con_{obj.contract.pk}", obj.contract.name, "contract",
                                   f"/assets/contracts/{obj.contract.uuid}/"))
                edges.append(_edge(f"con_{obj.contract.pk}", f"ev_{obj.pk}", "contract"))

    except Exception:
        pass

    return JsonResponse({"nodes": nodes, "edges": edges})


# ---------------------------------------------------------------------------
# Transition views (POST only)
# ---------------------------------------------------------------------------

def _actor(request):
    return request.user if request.user.is_authenticated else None


def entitlement_transition(request, uuid):
    from .services.lifecycle import revoke_entitlement, suspend_entitlement, reactivate_entitlement
    entitlement = get_object_or_404(Entitlement, uuid=uuid)
    if request.method == "POST":
        action = request.POST.get("action", "")
        reason = request.POST.get("reason", "").strip()
        try:
            if action == "revoke":
                revoke_entitlement(entitlement, actor=_actor(request), reason=reason)
                messages.success(request, "Entitlement revoked.")
            elif action == "suspend":
                suspend_entitlement(entitlement, actor=_actor(request), reason=reason)
                messages.success(request, "Entitlement suspended.")
            elif action == "reactivate":
                reactivate_entitlement(entitlement, actor=_actor(request))
                messages.success(request, "Entitlement reactivated.")
            else:
                messages.error(request, f"Unknown action: {action!r}")
        except ValidationError as e:
            messages.error(request, str(e.message))
    return redirect("claims:entitlement_detail", uuid=uuid)


def schedule_transition(request, uuid):
    from .services.lifecycle import transition_schedule
    from .models import ScheduleStatus
    schedule = get_object_or_404(Schedule, uuid=uuid)
    if request.method == "POST":
        action = request.POST.get("action", "")
        reason = request.POST.get("reason", "").strip()
        target_map = {
            "activate": ScheduleStatus.ACTIVE,
            "pause":    ScheduleStatus.PAUSED,
            "resume":   ScheduleStatus.ACTIVE,
            "complete": ScheduleStatus.COMPLETED,
            "cancel":   ScheduleStatus.CANCELLED,
        }
        target = target_map.get(action)
        if target:
            try:
                transition_schedule(schedule, target, actor=_actor(request), reason=reason)
                messages.success(request, f"Schedule {action}d.")
            except ValidationError as e:
                messages.error(request, str(e.message))
        else:
            messages.error(request, f"Unknown action: {action!r}")
    return redirect("claims:schedule_detail", uuid=uuid)


def condition_transition(request, uuid):
    from .services.lifecycle import (
        mark_condition_satisfied, mark_condition_failed,
        waive_condition, cancel_condition, _emit_condition_event,
    )
    condition = get_object_or_404(Condition, uuid=uuid)
    if request.method == "POST":
        action = request.POST.get("action", "")
        reason = request.POST.get("reason", "").strip()
        try:
            if action == "satisfy":
                mark_condition_satisfied(condition)
                _emit_condition_event(
                    condition, ContractEventKind.CONDITION_SATISFIED, actor=_actor(request)
                )
                messages.success(request, "Condition marked satisfied.")
            elif action == "fail":
                mark_condition_failed(condition, reason=reason)
                _emit_condition_event(
                    condition, ContractEventKind.CONDITION_FAILED,
                    actor=_actor(request), payload={"reason": reason} if reason else {},
                )
                messages.success(request, "Condition marked failed.")
            elif action == "waive":
                waive_condition(condition, reason=reason)
                _emit_condition_event(
                    condition, ContractEventKind.CUSTOM,
                    actor=_actor(request), payload={"reason": reason} if reason else {},
                )
                messages.success(request, "Condition waived.")
            elif action == "cancel":
                cancel_condition(condition, actor=_actor(request), reason=reason)
                messages.success(request, "Condition cancelled.")
            else:
                messages.error(request, f"Unknown action: {action!r}")
        except ValidationError as e:
            messages.error(request, str(e.message))
    return redirect("claims:condition_detail", uuid=uuid)


def allocation_transition(request, uuid):
    from .services.lifecycle import (
        activate_allocation, release_allocation,
        consume_allocation, cancel_allocation, _check_allocation_transition,
        create_event,
    )
    allocation = get_object_or_404(Allocation, uuid=uuid)
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "activate":
                _check_allocation_transition(allocation, AllocationStatus.ACTIVE)
                activate_allocation(allocation)
                create_event(
                    kind=ContractEventKind.ALLOCATION_CREATED,
                    title=f"Allocation activated: {allocation.get_kind_display()}",
                    allocation=allocation,
                    actor=_actor(request),
                    source_type="claims.Allocation",
                    source_id=str(allocation.pk),
                )
                messages.success(request, "Allocation activated.")
            elif action == "release_all":
                _check_allocation_transition(allocation, AllocationStatus.RELEASED)
                remaining = allocation.remaining_amount_base_units
                if remaining > 0:
                    release_allocation(allocation, remaining)
                else:
                    allocation.status = AllocationStatus.RELEASED
                    allocation.save(update_fields=["status", "updated_at"])
                create_event(
                    kind=ContractEventKind.ALLOCATION_RELEASED,
                    title=f"Allocation released: {allocation.get_kind_display()}",
                    allocation=allocation,
                    actor=_actor(request),
                    source_type="claims.Allocation",
                    source_id=str(allocation.pk),
                )
                messages.success(request, "Allocation released.")
            elif action == "consume_all":
                _check_allocation_transition(allocation, AllocationStatus.CONSUMED)
                remaining = allocation.remaining_amount_base_units
                if remaining > 0:
                    consume_allocation(allocation, remaining)
                else:
                    allocation.status = AllocationStatus.CONSUMED
                    allocation.save(update_fields=["status", "updated_at"])
                create_event(
                    kind=ContractEventKind.ALLOCATION_CONSUMED,
                    title=f"Allocation consumed: {allocation.get_kind_display()}",
                    allocation=allocation,
                    actor=_actor(request),
                    source_type="claims.Allocation",
                    source_id=str(allocation.pk),
                )
                messages.success(request, "Allocation consumed.")
            elif action == "cancel":
                _check_allocation_transition(allocation, AllocationStatus.CANCELLED)
                cancel_allocation(allocation)
                create_event(
                    kind=ContractEventKind.CANCELLED,
                    title=f"Allocation cancelled: {allocation.get_kind_display()}",
                    allocation=allocation,
                    actor=_actor(request),
                    source_type="claims.Allocation",
                    source_id=str(allocation.pk),
                )
                messages.success(request, "Allocation cancelled.")
            else:
                messages.error(request, f"Unknown action: {action!r}")
        except ValidationError as e:
            messages.error(request, str(e.message))
    return redirect("claims:allocation_detail", uuid=uuid)
