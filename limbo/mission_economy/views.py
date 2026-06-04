from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone

from toto.kanban.models import Mission, Practitioner
from toto.ui import PageProcessor

from .forms import ProjectTokenizationCreateForm, ProjectTokenizationDefaultForm
from .models import ProjectTokenization, ProjectTokenizationStatus


@login_required
def mission_economy(request, pk):
    mission = get_object_or_404(
        Mission.objects.select_related(
            "campaign__project", "campaign__owner", "owner",
        ),
        pk=pk,
    )
    project = mission.campaign.project

    from toto.contracts.models import Contract, ContractNode

    linked_nodes = ContractNode.objects.filter(
        object_app="kanban",
        object_model="mission",
        object_id=str(mission.pk),
    ).select_related("contract")
    linked_contracts = [n.contract for n in linked_nodes]
    linked_contract_ids = {c.pk for c in linked_contracts}

    practitioners = (
        Practitioner.objects
        .filter(assigned_tasks__mission=mission, is_active=True)
        .select_related("person")
        .distinct()
    )

    def _payroll_for(person):
        return list(
            Contract.objects.filter(
                metadata__archetype="payroll",
                nodes__key="worker_person",
                nodes__object_app="people",
                nodes__object_model="person",
                nodes__object_id=str(person.pk),
            ).distinct()
        )

    practitioner_rows = [
        {"practitioner": p, "payroll": _payroll_for(p.person)}
        for p in practitioners
    ]

    tokenization = getattr(project, "tokenization", None)
    all_contracts = Contract.objects.exclude(pk__in=linked_contract_ids).order_by("name")

    budget_summary = []
    for contract in linked_contracts:
        meta = contract.metadata or {}
        archetype = meta.get("archetype", "")
        entry = {"contract": contract, "archetype": archetype, "lines": []}
        if archetype == "payroll":
            allocation = meta.get("allocation_base_units")
            asset_unit = meta.get("asset_unit_name", "")
            if allocation is not None:
                entry["lines"].append({"label": "Allocation", "value": f"{allocation} {asset_unit}".strip()})
            frequency = meta.get("frequency", "")
            if frequency:
                entry["lines"].append({"label": "Frequency", "value": frequency.title()})
        elif archetype == "insurance":
            premium = meta.get("premium_amount_base_units")
            asset_unit = meta.get("premium_asset_unit", "")
            if premium is not None:
                entry["lines"].append({"label": "Premium", "value": f"{premium} {asset_unit}".strip()})
            freq = meta.get("premium_frequency", "")
            if freq:
                entry["lines"].append({"label": "Frequency", "value": freq.title()})
        elif archetype == "loan":
            principal = meta.get("principal_base_units")
            asset_unit = meta.get("loan_asset_unit", "")
            if principal is not None:
                entry["lines"].append({"label": "Principal", "value": f"{principal} {asset_unit}".strip()})
            rate_bps = meta.get("interest_rate_bps")
            if rate_bps is not None:
                entry["lines"].append({"label": "Rate", "value": f"{rate_bps / 100:.2f}%"})
        budget_summary.append(entry)

    from toto.claims.models import ContractEvent
    cost_history = (
        ContractEvent.objects
        .filter(contract_id__in=linked_contract_ids)
        .select_related("contract", "actor")
        .order_by("-created_at")[:20]
    ) if linked_contract_ids else []

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "link":
            contract_id = request.POST.get("contract_id")
            if contract_id:
                contract = get_object_or_404(Contract, pk=contract_id)
                ContractNode.objects.get_or_create(
                    contract=contract,
                    key="linked_mission",
                    object_app="kanban",
                    object_model="mission",
                    object_id=str(mission.pk),
                    defaults={
                        "node_type": "manual",
                        "title": f"Mission: {mission.title}",
                        "is_manual": True,
                    },
                )
                messages.success(request, f'Contract "{contract.name}" linked to mission.')

        elif action == "unlink":
            contract_id = request.POST.get("contract_id")
            ContractNode.objects.filter(
                contract_id=contract_id,
                object_app="kanban",
                object_model="mission",
                object_id=str(mission.pk),
            ).delete()
            messages.success(request, "Contract unlinked.")

        return redirect("mission_economy:mission_economy", pk=pk)

    context = PageProcessor().decorate({
        "mission": mission,
        "project": project,
        "linked_contracts": linked_contracts,
        "all_contracts": all_contracts,
        "practitioner_rows": practitioner_rows,
        "tokenization": tokenization,
        "budget_summary": budget_summary,
        "cost_history": cost_history,
    }, request)
    return render(request, "mission_economy/mission_economy.html", context)


@login_required
def project_tokenize(request, pk):
    from toto.kanban.models import Project
    project = get_object_or_404(Project.objects.select_related("project_lead"), pk=pk)

    existing = getattr(project, "tokenization", None)
    if existing:
        messages.info(request, "This project is already tokenized.")
        return redirect("assets:asset_detail", pk=existing.asset_id)

    if request.method == "POST":
        form = ProjectTokenizationCreateForm(request.POST, project=project)
        if form.is_valid():
            data = form.cleaned_data
            metadata = data.get("metadata") or {}
            try:
                with transaction.atomic():
                    from toto.assets.services.assets import create_asset
                    asset = create_asset(
                        name=data["asset_name"],
                        unit_name=data["unit_name"],
                        total_supply=data["total_supply"],
                        decimals=data["decimals"],
                        reserve_account=data["reserve_account"],
                        reference=f"project-{project.pk}-{data['unit_name'].lower()}",
                        description=f"Token for project: {project.name}",
                        metadata={
                            **metadata,
                            "tokenized_project_id": project.pk,
                            "tokenized_project_name": project.name,
                        },
                    )
                    asset.is_currency = data.get("is_currency", False)
                    asset.backing_document = data.get("backing_document", "")
                    asset.minting_authority = data.get("minting_authority", "")
                    asset.save(update_fields=["is_currency", "backing_document", "minting_authority", "updated_at"])
                    ProjectTokenization.objects.create(
                        project=project,
                        asset=asset,
                        supervisor=data.get("supervisor"),
                        metadata=metadata,
                    )
            except ValidationError as exc:
                form.add_error(None, exc.messages[0] if hasattr(exc, "messages") else str(exc))
            else:
                messages.success(request, f"{project.name} tokenized as {asset.unit_name}.")
                return redirect("assets:asset_detail", pk=asset.pk)
    else:
        slug = "".join(ch for ch in project.name.upper() if ch.isalnum())[:12] or f"PRJ{project.pk}"
        form = ProjectTokenizationCreateForm(
            project=project,
            initial={
                "asset_name": f"{project.name} Token",
                "unit_name": slug,
                "decimals": 0,
                "total_supply": 1000000,
                "backing_document": project.description,
            },
        )

    context = PageProcessor().decorate({"project": project, "form": form}, request)
    return render(request, "mission_economy/project_tokenize.html", context)


@require_POST
@login_required
def project_tokenization_default(request, pk):
    tokenization = get_object_or_404(
        ProjectTokenization.objects.select_related("asset", "project"),
        pk=pk,
    )

    if tokenization.status == ProjectTokenizationStatus.DEFAULTED:
        messages.warning(request, "Tokenization is already defaulted.")
        return redirect("kanban:project_detail", pk=tokenization.project_id)

    form = ProjectTokenizationDefaultForm(request.POST)
    if form.is_valid():
        tokenization.status = ProjectTokenizationStatus.DEFAULTED
        tokenization.default_reason = form.cleaned_data["reason"]
        tokenization.default_note = form.cleaned_data.get("note", "")
        tokenization.defaulted_at = timezone.now()
        tokenization.defaulted_by = request.user
        tokenization.save(update_fields=["status", "default_reason", "default_note", "defaulted_at", "defaulted_by"])
        messages.success(request, f"Tokenization for {tokenization.project.name} has been defaulted.")

    return redirect("kanban:project_detail", pk=tokenization.project_id)
