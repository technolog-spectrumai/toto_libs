from __future__ import annotations

import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from toto.ui import PageProcessor
from toto.people.models import Person

from .forms import ContractForm, ContractNodeForm, ContractEdgeForm, ContractSignatoryForm
from .models import Contract, ContractNode, ContractEdge, ContractSignatory
from .services import contract_to_cytoscape


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def contract_list(request):
    q = request.GET.get("q", "").strip()
    qs = Contract.objects.prefetch_related("nodes", "edges")
    if q:
        qs = qs.filter(name__icontains=q)

    contracts_data = []
    for c in qs:
        nodes = list(c.nodes.all())
        manual_count = sum(1 for n in nodes if n.is_manual)
        type_counts = {}
        for n in nodes:
            type_counts[n.node_type] = type_counts.get(n.node_type, 0) + 1
        contracts_data.append({
            "contract": c,
            "node_count": len(nodes),
            "edge_count": c.edges.count(),
            "manual_count": manual_count,
            "type_counts": type_counts,
        })

    return _render(request, "contracts/contract_list.html", {
        "contracts_data": contracts_data,
        "q": q,
    })


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

def contract_detail(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid)
    nodes = list(contract.nodes.all())
    edges = list(contract.edges.select_related("source", "target").all())
    manual_count = sum(1 for n in nodes if n.is_manual)
    type_counts: dict[str, int] = {}
    for n in nodes:
        type_counts[n.node_type] = type_counts.get(n.node_type, 0) + 1

    signatories = list(contract.signatories.select_related("person").all())
    current_person = _get_person_for_request(request)
    current_person_signatory = next((s for s in signatories if current_person and s.person_id == current_person.pk), None)

    return _render(request, "contracts/contract_detail.html", {
        "contract": contract,
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "manual_count": manual_count,
        "type_counts": type_counts,
        "signatories": signatories,
        "current_person_signatory": current_person_signatory,
    })


# ---------------------------------------------------------------------------
# Create / Update
# ---------------------------------------------------------------------------

def contract_create(request):
    if request.method == "POST":
        form = ContractForm(request.POST)
        if form.is_valid():
            contract = form.save()
            return redirect("contracts:contract_detail", uuid=contract.uuid)
    else:
        form = ContractForm()
    return _render(request, "contracts/contract_form.html", {
        "form": form,
        "is_create": True,
    })


def contract_update(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid)
    if request.method == "POST":
        form = ContractForm(request.POST, instance=contract)
        if form.is_valid():
            form.save()
            return redirect("contracts:contract_detail", uuid=contract.uuid)
    else:
        form = ContractForm(instance=contract)
    return _render(request, "contracts/contract_form.html", {
        "form": form,
        "contract": contract,
        "is_create": False,
    })


# ---------------------------------------------------------------------------
# Graph JSON
# ---------------------------------------------------------------------------

def contract_graph_json(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid)
    return JsonResponse(contract_to_cytoscape(contract))


# ---------------------------------------------------------------------------
# Node create / update
# ---------------------------------------------------------------------------

def contract_node_create(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid)
    if request.method == "POST":
        form = ContractNodeForm(request.POST, contract=contract)
        if form.is_valid():
            node = form.save(commit=False)
            node.contract = contract
            node.save()
            return redirect("contracts:contract_detail", uuid=contract.uuid)
    else:
        form = ContractNodeForm(contract=contract)
    return _render(request, "contracts/contract_node_form.html", {
        "form": form,
        "contract": contract,
        "is_create": True,
    })


def contract_node_update(request, uuid, pk):
    contract = get_object_or_404(Contract, uuid=uuid)
    node = get_object_or_404(ContractNode, pk=pk, contract=contract)
    if request.method == "POST":
        form = ContractNodeForm(request.POST, instance=node, contract=contract)
        if form.is_valid():
            form.save()
            return redirect("contracts:contract_detail", uuid=contract.uuid)
    else:
        form = ContractNodeForm(instance=node, contract=contract)
    return _render(request, "contracts/contract_node_form.html", {
        "form": form,
        "contract": contract,
        "node": node,
        "is_create": False,
    })


# ---------------------------------------------------------------------------
# Edge create / update
# ---------------------------------------------------------------------------

def contract_edge_create(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid)
    if request.method == "POST":
        form = ContractEdgeForm(request.POST, contract=contract)
        if form.is_valid():
            edge = form.save(commit=False)
            edge.contract = contract
            edge.save()
            return redirect("contracts:contract_detail", uuid=contract.uuid)
    else:
        form = ContractEdgeForm(contract=contract)
    return _render(request, "contracts/contract_edge_form.html", {
        "form": form,
        "contract": contract,
        "is_create": True,
    })


def contract_edge_update(request, uuid, pk):
    contract = get_object_or_404(Contract, uuid=uuid)
    edge = get_object_or_404(ContractEdge, pk=pk, contract=contract)
    if request.method == "POST":
        form = ContractEdgeForm(request.POST, instance=edge, contract=contract)
        if form.is_valid():
            form.save()
            return redirect("contracts:contract_detail", uuid=contract.uuid)
    else:
        form = ContractEdgeForm(instance=edge, contract=contract)
    return _render(request, "contracts/contract_edge_form.html", {
        "form": form,
        "contract": contract,
        "edge": edge,
        "is_create": False,
    })


# ---------------------------------------------------------------------------
# Signatories
# ---------------------------------------------------------------------------

def contract_add_signatory(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid)
    if request.method == "POST":
        form = ContractSignatoryForm(request.POST, contract=contract)
        if form.is_valid():
            signatory = form.save(commit=False)
            signatory.contract = contract
            signatory.save()
            if contract.status == Contract.STATUS_DRAFT:
                contract.status = Contract.STATUS_PENDING
                contract.save(update_fields=["status"])
            messages.success(request, f"{signatory.person} added as signatory.")
            return redirect("contracts:contract_detail", uuid=contract.uuid)
    else:
        form = ContractSignatoryForm(contract=contract)
    return _render(request, "contracts/contract_signatory_form.html", {
        "form": form,
        "contract": contract,
    })


def contract_remove_signatory(request, uuid, pk):
    contract = get_object_or_404(Contract, uuid=uuid)
    signatory = get_object_or_404(ContractSignatory, pk=pk, contract=contract)
    if request.method == "POST":
        signatory.delete()
        messages.success(request, "Signatory removed.")
    return redirect("contracts:contract_detail", uuid=contract.uuid)


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def _get_person_for_request(request):
    """Return the Person linked to the logged-in user, or None."""
    if not request.user.is_authenticated:
        return None
    try:
        return request.user.community_profile
    except Exception:
        return None


def contract_sign(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid)
    person = _get_person_for_request(request)

    signatory = None
    if person:
        signatory = ContractSignatory.objects.filter(contract=contract, person=person).first()

    if request.method == "POST":
        if not person:
            messages.error(request, "You must have a Person profile to sign contracts.")
            return redirect("contracts:contract_detail", uuid=contract.uuid)
        if not signatory:
            messages.error(request, "You are not listed as a signatory for this contract.")
            return redirect("contracts:contract_detail", uuid=contract.uuid)
        if signatory.has_signed:
            messages.info(request, "You have already signed this contract.")
            return redirect("contracts:contract_detail", uuid=contract.uuid)

        signature_data = request.POST.get("signature_data", "").strip()

        # Persist signature on signatory record
        signatory.signed_at = timezone.now()
        signatory.signature_data = signature_data
        signatory.save(update_fields=["signed_at", "signature_data"])

        # Also update person's default digital signature if they don't have one
        if signature_data and not person.digital_signature:
            person.digital_signature = signature_data
            person.save(update_fields=["digital_signature"])

        # Check if contract should be auto-executed
        contract.check_and_execute()

        messages.success(request, "Contract signed successfully.")
        return redirect("contracts:contract_detail", uuid=contract.uuid)

    return _render(request, "contracts/contract_sign.html", {
        "contract": contract,
        "person": person,
        "signatory": signatory,
        "existing_signature": person.digital_signature if person else "",
    })


def person_update_signature(request, person_pk):
    """Allow a person to update their stored digital signature."""
    person = get_object_or_404(Person, pk=person_pk)
    if request.method == "POST":
        signature_data = request.POST.get("signature_data", "").strip()
        if signature_data:
            person.digital_signature = signature_data
            person.save(update_fields=["digital_signature"])
            messages.success(request, "Signature saved.")
        return redirect(request.META.get("HTTP_REFERER", "/"))
    return _render(request, "contracts/person_signature_form.html", {
        "person": person,
    })
