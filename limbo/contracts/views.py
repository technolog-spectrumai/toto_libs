from __future__ import annotations

import json
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import TemplateView

from toto.ui import PageProcessor
from toto.people.models import Person

from .forms import (
    ContractForm, ContractNodeForm, ContractEdgeForm, ContractSignatoryForm,
    PayrollCreateForm, PayrollDutyForm, InsuranceCreateForm, LoanCreateForm, LeaseCreateForm,
)
from .models import Contract, ContractNode, ContractEdge, ContractSignatory
from .services import contract_to_cytoscape, evaluate_claims_health


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _get_person_for_request(request):
    if not request.user.is_authenticated:
        return None
    try:
        return request.user.community_profile
    except Exception:
        return None


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

    from toto.gervazy.models import EncryptedFile
    user_encrypted_files = []
    if request.user.is_authenticated:
        user_encrypted_files = list(
            EncryptedFile.objects.filter(owner=request.user, state="active")
            .select_related("strongbox")
            .order_by("-uploaded_at")
        )

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
        "user_encrypted_files": user_encrypted_files,
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

def contract_sign(request, uuid):
    from toto.gervazy.models import UserStrongbox
    from toto.gervazy.signing import SigningService, SigningError
    from toto.gervazy.crypto import GervazyCryptoSession

    contract = get_object_or_404(Contract, uuid=uuid)
    person = _get_person_for_request(request)

    signatory = None
    if person:
        signatory = ContractSignatory.objects.filter(contract=contract, person=person).first()

    strongboxes = []
    existing_signing_key = None
    if person and request.user.is_authenticated:
        strongboxes = list(UserStrongbox.objects.filter(owner=request.user))
        existing_signing_key = SigningService.get_active_signing_key(person)

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

        strongbox_id = request.POST.get("strongbox_id", "").strip()
        password = request.POST.get("strongbox_password", "").strip()
        signature_data = request.POST.get("signature_data", "").strip()

        if not password:
            messages.error(request, "Strongbox password is required to sign.")
            return _render(request, "contracts/contract_sign.html", _sign_ctx(
                contract, person, signatory, strongboxes, existing_signing_key,
            ))

        from toto.gervazy.models import WrappedDataKey

        existing_signing_key = SigningService.get_active_signing_key(person)

        if existing_signing_key:
            signing_strongbox = existing_signing_key.encrypted_private_key.strongbox
            wrapped_key = None
        else:
            try:
                if strongbox_id:
                    signing_strongbox = UserStrongbox.objects.get(pk=strongbox_id, owner=request.user)
                elif strongboxes:
                    signing_strongbox = strongboxes[0]
                else:
                    messages.error(request, "No strongbox found. Set one up in Gervazy first.")
                    return redirect("contracts:contract_detail", uuid=contract.uuid)
            except UserStrongbox.DoesNotExist:
                messages.error(request, "Invalid strongbox selection.")
                return _render(request, "contracts/contract_sign.html", _sign_ctx(
                    contract, person, signatory, strongboxes, existing_signing_key,
                ))

            wrapped_key = (
                WrappedDataKey.objects
                .filter(strongbox=signing_strongbox, state="active")
                .select_related("vmk")
                .first()
            )
            if not wrapped_key:
                messages.error(
                    request,
                    f"No active data key found in strongbox \"{signing_strongbox.name}\". "
                    "Initialize a data key in Gervazy before signing for the first time.",
                )
                return _render(request, "contracts/contract_sign.html", _sign_ctx(
                    contract, person, signatory, strongboxes, existing_signing_key,
                ))

        try:
            session = GervazyCryptoSession(signing_strongbox, password)

            signed_at = timezone.now()
            payload = SigningService.canonical_contract_payload(contract, person, signed_at)
            doc_sig = SigningService.sign_document(session, person, payload, wrapped_key=wrapped_key)

            signatory.signed_at = signed_at
            signatory.signature_data = signature_data
            signatory.signing_payload = payload.decode("utf-8")
            signatory.cryptographic_signature = doc_sig.signature_b64
            signatory.signing_key = (
                SigningService.get_active_signing_key(person).encrypted_private_key
            )
            update_fields = [
                "signed_at", "signature_data",
                "signing_payload", "cryptographic_signature", "signing_key",
            ]
            signatory.save(update_fields=update_fields)

            if signature_data and not person.digital_signature:
                person.digital_signature = signature_data
                person.save(update_fields=["digital_signature"])

            session.close()

        except Exception as exc:
            messages.error(request, f"Signing failed: {exc}")
            return _render(request, "contracts/contract_sign.html", _sign_ctx(
                contract, person, signatory, strongboxes, existing_signing_key,
            ))

        contract.check_and_execute()
        messages.success(request, "Contract signed and cryptographic signature recorded.")
        return redirect("contracts:contract_detail", uuid=contract.uuid)

    return _render(request, "contracts/contract_sign.html", _sign_ctx(
        contract, person, signatory, strongboxes, existing_signing_key,
    ))


def _sign_ctx(contract, person, signatory, strongboxes, existing_signing_key):
    required_strongbox = None
    if existing_signing_key:
        required_strongbox = existing_signing_key.encrypted_private_key.strongbox
    return {
        "contract": contract,
        "person": person,
        "signatory": signatory,
        "strongboxes": strongboxes,
        "existing_signing_key": existing_signing_key,
        "required_strongbox": required_strongbox,
        "existing_signature": person.digital_signature if person else "",
    }


def contract_attach_pdf(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid)
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "detach":
            contract.vault_pdf = None
            contract.save(update_fields=["vault_pdf"])
            messages.success(request, "PDF detached.")
        elif action == "attach":
            from toto.gervazy.models import EncryptedFile
            file_id = request.POST.get("encrypted_file_id", "").strip()
            if file_id:
                try:
                    ef = EncryptedFile.objects.get(pk=file_id, state="active")
                    contract.vault_pdf = ef
                    contract.save(update_fields=["vault_pdf"])
                    messages.success(request, "PDF attached.")
                except EncryptedFile.DoesNotExist:
                    messages.error(request, "File not found.")
    return redirect("contracts:contract_detail", uuid=uuid)


def contract_download_pdf(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid)
    if not contract.vault_pdf:
        messages.error(request, "No vault PDF attached to this contract.")
        return redirect("contracts:contract_detail", uuid=uuid)

    if request.method == "POST":
        from toto.gervazy.crypto import GervazyCryptoSession
        password = request.POST.get("strongbox_password", "").strip()
        if not password:
            messages.error(request, "Password is required.")
            return _render(request, "contracts/contract_download_pdf.html", {"contract": contract})

        ef = contract.vault_pdf
        try:
            session = GervazyCryptoSession(ef.strongbox, password)
            plaintext, filename = session.decrypt_file(ef)
            session.close()
        except Exception as exc:
            messages.error(request, f"Decryption failed: {exc}")
            return _render(request, "contracts/contract_download_pdf.html", {"contract": contract})

        mime = ef.mime_type or "application/octet-stream"
        response = HttpResponse(plaintext, content_type=mime)
        safe_name = filename.replace('"', '_') if filename else f"contract_{contract.uuid}.pdf"
        response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
        return response

    return _render(request, "contracts/contract_download_pdf.html", {"contract": contract})


def contract_evaluate_health(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid)
    if request.method == "POST":
        result = evaluate_claims_health(contract)
        meta = dict(contract.metadata or {})
        meta["claims_health"] = result
        contract.metadata = meta
        contract.save(update_fields=["metadata"])
        messages.success(request, "Claims health evaluated.")
    return redirect("contracts:contract_detail", uuid=uuid)


# ---------------------------------------------------------------------------
# Person signature
# ---------------------------------------------------------------------------

def person_update_signature(request, person_pk):
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


# ---------------------------------------------------------------------------
# Payroll
# ---------------------------------------------------------------------------

def _payroll_ctx(contract):
    def _obj(key):
        n = contract.nodes.filter(key=key).first()
        return n.get_object() if n else None

    return {
        "contract": contract,
        "schedule": _obj("pay_schedule"),
        "allocation": _obj("budget"),
        "worker": _obj("worker_person"),
        "asset": _obj("payment_asset"),
        "payer_account": _obj("payer_acct"),
        "worker_account": _obj("worker_acct"),
    }


def _payroll_duties(contract):
    duty_nodes = contract.nodes.filter(node_type="obligation").order_by("key")
    duties = []
    for node in duty_nodes:
        obligation = node.get_object()
        if not obligation:
            continue
        gates_edge = (
            contract.edges
            .filter(target=node, edge_type="gates")
            .select_related("source")
            .first()
        )
        condition = gates_edge.source.get_object() if gates_edge else None
        duties.append({
            "node": node,
            "obligation": obligation,
            "condition": condition,
        })
    return duties


def payroll_list(request):
    qs = (
        Contract.objects
        .filter(metadata__archetype="payroll")
        .prefetch_related("nodes")
        .order_by("name")
    )
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(name__icontains=q)

    rows = []
    for c in qs:
        def _obj(key, _c=c):
            n = _c.nodes.filter(key=key).first()
            return n.get_object() if n else None

        rows.append({
            "contract": c,
            "worker": _obj("worker_person"),
            "asset": _obj("payment_asset"),
            "allocation": _obj("budget"),
            "duty_count": c.nodes.filter(node_type="obligation").count(),
        })

    return _render(request, "contracts/payroll_list.html", {"rows": rows, "q": q})


def payroll_create(request):
    if request.method == "POST":
        form = PayrollCreateForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            meta = {k: cd[k] for k in ("source_app", "source_model", "source_id") if cd.get(k)}
            from toto.payroll.services import create_payroll_contract
            contract = create_payroll_contract(
                name=cd["name"],
                payer_account=cd["payer_account"],
                worker_person=cd["worker_person"],
                worker_account=cd["worker_account"],
                asset=cd["asset"],
                amount_base_units=cd["amount_base_units"],
                frequency=cd["frequency"],
                metadata=meta or None,
            )
            messages.success(request, f"Payroll contract '{contract.name}' created.")
            return redirect("contracts:payroll_detail", uuid=contract.uuid)
    else:
        form = PayrollCreateForm()
    return _render(request, "contracts/payroll_form.html", {"form": form})


def payroll_detail(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="payroll")
    ctx = _payroll_ctx(contract)
    ctx["duties"] = _payroll_duties(contract)

    from toto.claims.models import ContractEvent
    ctx["events"] = list(
        ContractEvent.objects
        .filter(source_type="contracts.Contract", source_id=str(contract.pk))
        .order_by("-created_at")[:30]
    )

    signatories = list(contract.signatories.select_related("person").all())
    current_person = _get_person_for_request(request)
    ctx["signatories"] = signatories
    ctx["current_person_signatory"] = next(
        (s for s in signatories if current_person and s.person_id == current_person.pk), None
    )

    return _render(request, "contracts/payroll_detail.html", ctx)


def payroll_duty_create(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="payroll")
    ctx = _payroll_ctx(contract)

    if request.method == "POST":
        form = PayrollDutyForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            meta = {k: cd[k] for k in ("source_app", "source_id") if cd.get(k)}
            from toto.payroll.services import create_payroll_duty
            try:
                create_payroll_duty(
                    contract=contract,
                    worker_person=ctx["worker"],
                    worker_account=ctx["worker_account"],
                    amount_base_units=cd["amount_base_units"],
                    due_at=cd.get("due_at"),
                    metadata=meta or None,
                )
                messages.success(request, "Duty created.")
                return redirect("contracts:payroll_detail", uuid=uuid)
            except Exception as exc:
                messages.error(request, f"Could not create duty: {exc}")
    else:
        form = PayrollDutyForm()

    ctx["form"] = form
    return _render(request, "contracts/payroll_duty_form.html", ctx)


def payroll_duty_mark_due(request, uuid, obligation_pk):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="payroll")
    if request.method == "POST":
        node = get_object_or_404(
            ContractNode, contract=contract, node_type="obligation",
            object_id=str(obligation_pk),
        )
        from toto.payroll.services import mark_payroll_due
        try:
            mark_payroll_due(contract=contract, duty_node_key=node.key)
            messages.success(request, "Duty marked as due.")
        except Exception as exc:
            messages.error(request, f"Error: {exc}")
    return redirect("contracts:payroll_detail", uuid=uuid)


def payroll_duty_approve(request, uuid, obligation_pk):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="payroll")
    if request.method == "POST":
        node = get_object_or_404(
            ContractNode, contract=contract, node_type="obligation",
            object_id=str(obligation_pk),
        )
        gates_edge = (
            contract.edges
            .filter(target=node, edge_type="gates")
            .select_related("source")
            .first()
        )
        if not gates_edge:
            messages.error(request, "No approval condition found for this duty.")
            return redirect("contracts:payroll_detail", uuid=uuid)
        condition = gates_edge.source.get_object()
        from toto.payroll.services import approve_payroll_duty
        try:
            approve_payroll_duty(
                condition=condition,
                approver=request.user if request.user.is_authenticated else None,
            )
            messages.success(request, "Duty approved.")
        except Exception as exc:
            messages.error(request, f"Error: {exc}")
    return redirect("contracts:payroll_detail", uuid=uuid)


def payroll_duty_settle(request, uuid, obligation_pk):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="payroll")
    if request.method == "POST":
        from toto.assets.models import Obligation
        obligation = get_object_or_404(Obligation, pk=obligation_pk)
        from toto.payroll.services import settle_payroll_duty
        try:
            settle_payroll_duty(obligation=obligation, contract=contract)
            messages.success(request, "Duty settled.")
        except Exception as exc:
            messages.error(request, f"Settlement failed: {exc}")
    return redirect("contracts:payroll_detail", uuid=uuid)


# ---------------------------------------------------------------------------
# Loans
# ---------------------------------------------------------------------------

def _loan_ctx(contract):
    def _obj(key):
        n = contract.nodes.filter(key=key).first()
        return n.get_object() if n else None

    meta = contract.metadata or {}
    interest_bps = meta.get("interest_rate_bps", 0)

    return {
        "contract": contract,
        "lender_person": _obj("lender_person"),
        "borrower_person": _obj("borrower_person"),
        "lender_account": _obj("lender_acct"),
        "borrower_account": _obj("borrower_acct"),
        "loan_asset": _obj("loan_asset"),
        "principal_base_units": meta.get("principal_base_units"),
        "interest_rate_bps": interest_bps,
        "interest_rate_pct": interest_bps / 100 if interest_bps else 0,
        "repayment_frequency": meta.get("repayment_frequency", "—"),
        "term_months": meta.get("term_months"),
    }


def loan_list(request):
    qs = (
        Contract.objects
        .filter(metadata__archetype="loan")
        .prefetch_related("nodes")
        .order_by("name")
    )
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(name__icontains=q)

    rows = []
    for c in qs:
        def _obj(key, _c=c):
            n = _c.nodes.filter(key=key).first()
            return n.get_object() if n else None

        meta = c.metadata or {}
        rows.append({
            "contract": c,
            "lender": _obj("lender_person"),
            "borrower": _obj("borrower_person"),
            "loan_asset": _obj("loan_asset"),
            "principal_base_units": meta.get("principal_base_units"),
            "interest_rate_bps": meta.get("interest_rate_bps", 0),
            "term_months": meta.get("term_months"),
            "repayment_frequency": meta.get("repayment_frequency", "—"),
        })

    return _render(request, "contracts/loan_list.html", {"rows": rows, "q": q})


def loan_create(request):
    if request.method == "POST":
        form = LoanCreateForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            from toto.loans.services import create_loan_contract
            contract = create_loan_contract(
                name=cd["name"],
                lender_person=cd["lender_person"],
                borrower_person=cd["borrower_person"],
                lender_account=cd["lender_account"],
                borrower_account=cd["borrower_account"],
                loan_asset=cd["loan_asset"],
                principal_base_units=cd["principal_base_units"],
                interest_rate_bps=cd["interest_rate_bps"],
                repayment_frequency=cd["repayment_frequency"],
                term_months=cd["term_months"],
            )
            messages.success(request, f"Loan contract '{contract.name}' created.")
            return redirect("contracts:loan_detail", uuid=contract.uuid)
    else:
        form = LoanCreateForm()
    return _render(request, "contracts/loan_form.html", {"form": form})


def loan_detail(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="loan")
    ctx = _loan_ctx(contract)

    signatories = list(contract.signatories.select_related("person").all())
    current_person = _get_person_for_request(request)
    ctx["signatories"] = signatories
    ctx["current_person_signatory"] = next(
        (s for s in signatories if current_person and s.person_id == current_person.pk), None
    )

    return _render(request, "contracts/loan_detail.html", ctx)


# ---------------------------------------------------------------------------
# Insurance
# ---------------------------------------------------------------------------

def _insurance_ctx(contract):
    def _obj(key):
        n = contract.nodes.filter(key=key).first()
        return n.get_object() if n else None

    insured_items = [
        n.get_object()
        for n in contract.nodes.filter(key__startswith="insured_item_").order_by("key")
        if n.get_object()
    ]
    insured_financial_assets = [
        n.get_object()
        for n in contract.nodes.filter(key__startswith="insured_fasset_").order_by("key")
        if n.get_object()
    ]

    return {
        "contract": contract,
        "insured_person": _obj("insured_person"),
        "payer_account": _obj("payer_acct"),
        "insurer_account": _obj("insurer_acct"),
        "premium_asset": _obj("premium_asset"),
        "insured_items": insured_items,
        "insured_financial_assets": insured_financial_assets,
        "premium_frequency": contract.metadata.get("premium_frequency", "—"),
        "premium_amount_base_units": contract.metadata.get("premium_amount_base_units"),
        "total_estimated_value": contract.metadata.get("total_estimated_value"),
    }


def insurance_list(request):
    qs = (
        Contract.objects
        .filter(metadata__archetype="insurance")
        .prefetch_related("nodes")
        .order_by("name")
    )
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(name__icontains=q)

    rows = []
    for c in qs:
        def _obj(key, _c=c):
            n = _c.nodes.filter(key=key).first()
            return n.get_object() if n else None

        rows.append({
            "contract": c,
            "insured_person": _obj("insured_person"),
            "premium_asset": _obj("premium_asset"),
            "item_count": (
                c.nodes.filter(key__startswith="insured_item_").count()
                + c.nodes.filter(key__startswith="insured_fasset_").count()
            ),
            "premium_frequency": c.metadata.get("premium_frequency", "—"),
            "premium_amount_base_units": c.metadata.get("premium_amount_base_units"),
        })

    return _render(request, "contracts/insurance_list.html", {"rows": rows, "q": q})


def insurance_create(request):
    if request.method == "POST":
        form = InsuranceCreateForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            from toto.insurance.services import create_insurance_contract
            contract = create_insurance_contract(
                name=cd["name"],
                insured_person=cd["insured_person"],
                payer_account=cd["payer_account"],
                insurer_account=cd["insurer_account"],
                premium_asset=cd["premium_asset"],
                premium_amount_base_units=cd["premium_amount_base_units"],
                premium_frequency=cd["premium_frequency"],
                insured_items=list(cd["insured_items"] or []),
                insured_financial_assets=list(cd["insured_financial_assets"] or []),
            )
            messages.success(request, f"Insurance contract '{contract.name}' created.")
            return redirect("contracts:insurance_detail", uuid=contract.uuid)
    else:
        form = InsuranceCreateForm()
    return _render(request, "contracts/insurance_form.html", {"form": form})


def insurance_detail(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="insurance")
    ctx = _insurance_ctx(contract)

    signatories = list(contract.signatories.select_related("person").all())
    current_person = _get_person_for_request(request)
    ctx["signatories"] = signatories
    ctx["current_person_signatory"] = next(
        (s for s in signatories if current_person and s.person_id == current_person.pk), None
    )

    return _render(request, "contracts/insurance_detail.html", ctx)


# ---------------------------------------------------------------------------
# Leasing
# ---------------------------------------------------------------------------

def _lease_ctx(contract):
    try:
        lease = contract.lease
    except Exception:
        lease = None
    return {
        "contract": contract,
        "lease": lease,
        "leased_object": lease.leased_object if lease else None,
        "lessor": lease.lessor if lease else None,
        "lessee": lease.lessee if lease else None,
    }


def lease_list(request):
    from toto.leasing.models import Lease
    qs = (
        Lease.objects
        .filter(contract__isnull=False)
        .select_related("leased_object", "lessor", "lessee", "contract", "payment_asset")
        .order_by("-created_at")
    )
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(leased_object__name__icontains=q)
    return _render(request, "contracts/lease_list.html", {"leases": qs, "q": q})


def lease_create(request):
    if request.method == "POST":
        form = LeaseCreateForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            from toto.leasing.services import create_lease_contract
            lease = create_lease_contract(
                name=cd["name"],
                leased_object=cd["leased_object"],
                lessor=cd["lessor"],
                lessee=cd["lessee"],
                billing_period=cd["billing_period"],
                fixed_fee_base_units=cd["fixed_fee_base_units"],
                starts_at=cd["starts_at"],
                ends_at=cd.get("ends_at"),
                lessor_account=cd.get("lessor_account"),
                lessee_account=cd.get("lessee_account"),
                payment_asset=cd.get("payment_asset"),
            )
            messages.success(request, f"Lease contract '{lease.contract.name}' created.")
            return redirect("contracts:lease_detail", uuid=lease.contract.uuid)
    else:
        form = LeaseCreateForm()
    return _render(request, "contracts/lease_form.html", {"form": form})


def lease_detail(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid)
    ctx = _lease_ctx(contract)

    signatories = list(contract.signatories.select_related("person").all())
    current_person = _get_person_for_request(request)
    ctx["signatories"] = signatories
    ctx["current_person_signatory"] = next(
        (s for s in signatories if current_person and s.person_id == current_person.pk), None
    )

    return _render(request, "contracts/lease_detail.html", ctx)
