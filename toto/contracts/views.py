from __future__ import annotations

import json

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
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

    # Encrypted files available for attaching as PDF (owned by current user).
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

def _get_person_for_request(request):
    """Return the Person linked to the logged-in user, or None."""
    if not request.user.is_authenticated:
        return None
    try:
        return request.user.community_profile
    except Exception:
        return None


def contract_sign(request, uuid):
    from toto.gervazy.models import UserStrongbox
    from toto.gervazy.signing import SigningService, SigningError
    from toto.gervazy.crypto import GervazyCryptoSession

    contract = get_object_or_404(Contract, uuid=uuid)
    person = _get_person_for_request(request)

    signatory = None
    if person:
        signatory = ContractSignatory.objects.filter(contract=contract, person=person).first()

    # Strongboxes available to this user (for key provisioning).
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

        # Determine which strongbox to open.
        # If the person already has a signing key, the session MUST be opened
        # against that key's strongbox (not whatever the user selected).
        existing_signing_key = SigningService.get_active_signing_key(person)

        if existing_signing_key:
            # Re-fetch with strongbox relation.
            signing_strongbox = existing_signing_key.encrypted_private_key.strongbox
            wrapped_key = None  # not needed — key already exists
        else:
            # Provisioning path — use selected (or first) strongbox and find a DEK.
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

        # Open session and sign.
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

            # Update person's decorative signature if they don't have one.
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
    # If a signing key exists, tell the template which strongbox password is needed.
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
    """POST: attach or detach an EncryptedFile as this contract's vault PDF."""
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
    """GET: show password form. POST: decrypt and serve the vault PDF."""
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
