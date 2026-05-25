from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from toto.ui import PageProcessor

from .forms import ContractForm, ContractNodeForm, ContractEdgeForm
from .models import Contract, ContractNode, ContractEdge
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

    return _render(request, "contracts/contract_detail.html", {
        "contract": contract,
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "manual_count": manual_count,
        "type_counts": type_counts,
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
