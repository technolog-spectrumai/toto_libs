"""
``.contract`` viewer, browser form editor, signing, and PDF conversion.

The ``.contract`` vault file is the single source of truth — these views never
touch the DB for contract content (only :class:`ContractTemplate` for PDF
rendering). Signing mirrors the constitution flow (electronic Ed25519 signature via
a gervazy strongbox + a handwritten appearance image).
"""
from __future__ import annotations

import hashlib
import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from toto.notarius import contract_format
from toto.ui import PageProcessor
from toto.vault.models import VaultFile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Read/write go through the storage with a FRESH handle each time, rather than the
# FieldFile's cached handle. Reading then writing the same VaultFile in one request
# otherwise leaves a stale/closed handle (UnsupportedOperation on write, or "I/O
# operation on closed file" on a second read).


def _read_raw(vf: VaultFile) -> str:
    """Raw UTF-8 text of the vault file via a fresh storage handle."""
    with vf.file.storage.open(vf.file.name, "rb") as fh:
        return fh.read().decode("utf-8")


def _read(vf: VaultFile) -> contract_format.Contract:
    try:
        return contract_format.loads(_read_raw(vf))
    except (contract_format.ContractParseError, UnicodeDecodeError, FileNotFoundError, ValueError):
        return contract_format.new_contract(title=vf.title)


def _write(vf: VaultFile, contract: contract_format.Contract) -> None:
    contract.content_hash = contract.computed_content_hash()
    xml = contract_format.dumps(contract)
    xml_bytes = xml.encode("utf-8")
    vf.file.close()  # drop any cached read handle from this request
    with vf.file.storage.open(vf.file.name, "w") as fh:
        fh.write(xml)
    vf.content_hash = hashlib.sha256(xml_bytes).hexdigest()
    vf.file_size_bytes = len(xml_bytes)
    vf.save(update_fields=["content_hash", "file_size_bytes"])


def _can_edit(request, vf) -> bool:
    return request.user.is_authenticated and vf.owner_id == request.user.id


def _can_read(request, vf: VaultFile) -> bool:
    """Read access: public, or (authenticated) owner / superuser / bucket-owner /
    directory whitelist — mirrors vault's accessible_files policy."""
    if vf.is_public:
        return True
    u = request.user
    if not u.is_authenticated:
        return False
    if u.is_superuser or vf.owner_id == u.id:
        return True
    if vf.bucket and vf.bucket.owner_id == u.id:
        return True
    if vf.directory and vf.directory.user_can_access(u):
        return True
    return False


def _get_file(request, file_pk, *, owner_only=False) -> VaultFile:
    """Fetch a contract vault file and validate the caller may open it.

    Contracts are ordinary ``.xml`` files now (``file_type="xml"``); the legacy
    ``contract`` type is still accepted. Only files whose content is a
    ``<signingDocument>`` open here, so arbitrary XML can't reach the contract views.
    """
    vf = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner"),
        pk=file_pk, file_type__in=["xml", "contract"],
    )
    try:
        if not contract_format.is_contract(_read_raw(vf)):
            raise Http404("Not a contract.")
    except (FileNotFoundError, UnicodeDecodeError, ValueError):
        raise Http404("Not a contract.")
    if owner_only:
        if not request.user.is_authenticated or vf.owner_id != request.user.id:
            raise Http404("Not your file.")
    elif not _can_read(request, vf):
        raise Http404("Not accessible.")
    return vf


def _current_person(request):
    if not request.user.is_authenticated:
        return None
    from toto.people.models import Person
    return Person.objects.filter(user=request.user).first()


def _user_emails(request, person=None) -> set[str]:
    """Lower-cased emails identifying the current user (account + Person)."""
    emails = set()
    if request.user.is_authenticated and request.user.email:
        emails.add(request.user.email.strip().lower())
    if person is None:
        person = _current_person(request)
    if person and getattr(person, "email", ""):
        emails.add(person.email.strip().lower())
    return {e for e in emails if e}


def _signable_party_ids(request, contract, vf) -> set[str]:
    """Party ids the current user may sign as: signer/issuer parties whose email
    matches the user's; or *every* signer/issuer party when the user owns the file
    or is a superuser."""
    signer_parties = [p for p in contract.parties if p.role in ("signer", "issuer")]
    u = request.user
    if u.is_authenticated and (u.is_superuser or vf.owner_id == u.id):
        return {p.id for p in signer_parties}
    emails = _user_emails(request)
    return {p.id for p in signer_parties if (p.email or "").strip().lower() in emails}


# ---------------------------------------------------------------------------
# Index / create
# ---------------------------------------------------------------------------

CONTRACT_LIST_CAP = 300


class NotariusIndexView(View):
    """List the contracts the current user can access. Contracts are ordinary
    ``.xml`` vault files whose root is ``<signingDocument>``."""

    template_name = "notarius/index.html"

    def get(self, request):
        from toto.vault.filetree import accessible_files
        from toto.vault.views import new_file_picker_json

        contracts = []
        if request.user.is_authenticated:
            # Scan the user's accessible XML files and keep the ones that are
            # contracts. Capped, so a large vault trades completeness for a bounded
            # page load (a DB-backed registry would remove the cap later).
            files = (
                accessible_files(request.user, file_types=["xml", "contract"])
                .filter(is_encrypted=False)
                .order_by("-uploaded_at")[:CONTRACT_LIST_CAP]
            )
            for vf in files:
                try:
                    raw = _read_raw(vf)
                except Exception:
                    continue
                if not contract_format.is_contract(raw):
                    continue
                try:
                    contract = contract_format.loads(raw)
                except contract_format.ContractParseError:
                    continue
                bname = vf.bucket.name if vf.bucket else "—"
                location = f"{bname} / {vf.directory.full_path()}" if vf.directory_id else bname
                contracts.append({
                    "title": contract.title or vf.title,
                    "status": contract.status,
                    "party_count": len(contract.parties),
                    "signature_count": len(contract.signatures),
                    "location": location,
                    "is_owner": vf.owner_id == request.user.id,
                    "can_sign": bool(_signable_party_ids(request, contract, vf))
                                and contract.status != "signed",
                    "view_url": reverse("notarius:view", args=[vf.pk]),
                    "edit_url": reverse("notarius:edit", args=[vf.pk]),
                    "sign_url": reverse("notarius:sign", args=[vf.pk]),
                })

        buckets_json, directories_json = new_file_picker_json(request.user)
        context = {
            "contracts": contracts,
            "buckets_json": buckets_json,
            "directories_json": directories_json,
            "create_url": reverse("notarius:create"),
        }
        return render(request, self.template_name, PageProcessor().decorate(context, request))


class ContractCreateView(LoginRequiredMixin, View):
    """Create a blank contract as an ordinary ``.xml`` vault file in a chosen
    bucket/directory, then open the editor. Reuses the shared new-file picker."""

    def post(self, request):
        from django.core.files.base import ContentFile
        from django.utils.text import slugify
        from toto.vault.views import _unique_file_key, resolve_new_file_target

        bucket, directory = resolve_new_file_target(
            request.user, request.POST.get("bucket_id"), request.POST.get("directory_id"),
        )
        raw_name = (request.POST.get("filename") or "").strip()
        base = raw_name[:-4] if raw_name.lower().endswith(".xml") else raw_name
        base = base.strip() or "contract"
        title = (request.POST.get("title") or "").strip() or base

        person = _current_person(request)
        issuer_name = (person.full_name if person else "") or request.user.get_username()
        issuer_email = (person.email if person and person.email else request.user.email) or ""

        contract = contract_format.new_contract(
            title=title, issuer_name=issuer_name, issuer_email=issuer_email,
        )
        xml_bytes = contract_format.dumps(contract).encode("utf-8")
        filename = f"{base}.xml"

        vf = VaultFile(
            owner=request.user, title=filename, key=_unique_file_key(slugify(base), bucket),
            file_type="xml", bucket=bucket, directory=directory, is_public=False,
        )
        vf.file.save(filename, ContentFile(xml_bytes), save=False)
        vf.content_hash = hashlib.sha256(xml_bytes).hexdigest()
        vf.file_size_bytes = len(xml_bytes)
        vf.save()
        return redirect("notarius:edit", file_pk=vf.pk)


# ---------------------------------------------------------------------------
# Viewer
# ---------------------------------------------------------------------------


class ContractView(View):
    """Read-only rendering of a ``.contract`` (parties, signatures, audit)."""

    template_name = "notarius/view.html"

    def get(self, request, file_pk):
        vf = _get_file(request, file_pk)
        contract = _read(vf)

        # Verify each electronic signature against its embedded public key — fully
        # self-contained (rebuild the canonical payload from the XML).
        from toto.gervazy.signing import SigningService
        sigs = []
        for s in contract.signatures:
            verified = None
            if s.public_key_pem and s.signature_value:
                payload = SigningService.canonical_contract_file_payload(
                    contract.id, contract.version, s.signed_hash, s.party, s.signed_at,
                )
                verified = SigningService.verify_with_public_key(
                    s.public_key_pem, payload, s.signature_value,
                )
            party = contract.party_by_id(s.party)
            sigs.append({"sig": s, "party": party, "verified": verified})

        can_edit = _can_edit(request, vf)
        context = {
            "vault_file": vf,
            "contract": contract,
            "signatures": sigs,
            "can_edit": can_edit,
            "edit_url": reverse("notarius:edit", args=[vf.pk]) if can_edit else "",
            "sign_url": reverse("notarius:sign", args=[vf.pk]),
            "convert_url": reverse("notarius:convert_pdf", args=[vf.pk]) if can_edit else "",
            "page_title": contract.title or vf.title,
        }
        return render(request, self.template_name, PageProcessor().decorate(context, request))


# ---------------------------------------------------------------------------
# Editor (nice form)
# ---------------------------------------------------------------------------


class ContractEditView(LoginRequiredMixin, View):
    """Browser form editor (owner only). Alpine-driven; saves JSON back as XML."""

    template_name = "notarius/edit.html"

    def get(self, request, file_pk):
        vf = _get_file(request, file_pk, owner_only=True)
        if vf.is_encrypted:
            from toto.vault.access import encrypted_lock_response
            return encrypted_lock_response(request, vf)
        contract = _read(vf)
        from toto.notarius.models import ContractTemplate
        context = {
            "vault_file": vf,
            "contract_json": contract.to_dict(),
            "template_keys": list(ContractTemplate.objects.values_list("key", "name")),
            "save_url": reverse("notarius:save", args=[vf.pk]),
            "view_url": reverse("notarius:view", args=[vf.pk]),
            "page_title": f"Edit — {contract.title or vf.title}",
        }
        return render(request, self.template_name, PageProcessor().decorate(context, request))


@csrf_exempt
def contract_save(request, file_pk):
    """Persist the edited contract (metadata, parties, content) back to the file.

    Signatures/audit are preserved from the on-disk file — the form never edits
    them (those come from the signing flow)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    vf = _get_file(request, file_pk, owner_only=True)
    if vf.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError) as exc:
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    existing = _read(vf)
    edited = contract_format.Contract.from_dict(payload)
    # Keep signatures + audit from disk; the editor only owns metadata/parties/content.
    edited.signatures = existing.signatures
    edited.audit = existing.audit
    if not edited.id:
        edited.id = existing.id or contract_format.new_contract().id
    if not edited.created_at:
        edited.created_at = existing.created_at or contract_format._now_iso()

    try:
        _write(vf, edited)
        return JsonResponse({"status": "ok", "view_url": reverse("notarius:view", args=[vf.pk])})
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


# ---------------------------------------------------------------------------
# Signing (electronic + handwritten)
# ---------------------------------------------------------------------------


class ContractSignView(LoginRequiredMixin, View):
    """Sign a contract: electronic Ed25519 signature (gervazy strongbox) + a
    handwritten appearance image. Mirrors socialhub constitution_sign."""

    template_name = "notarius/sign.html"

    def _ctx(self, request, vf, contract, person):
        from toto.gervazy.models import UserStrongbox
        from toto.gervazy.signing import SigningService
        strongboxes = list(UserStrongbox.objects.filter(owner=request.user)) if person else []
        existing_key = SigningService.get_active_signing_key(person) if person else None
        # Only the parties this user may sign as (email match, or owner/superuser).
        signable_ids = _signable_party_ids(request, contract, vf)
        signer_parties = [p for p in contract.parties if p.id in signable_ids]
        return {
            "vault_file": vf,
            "contract": contract,
            "person": person,
            "signer_parties": signer_parties,
            "strongboxes": strongboxes,
            "existing_signing_key": existing_key,
            "existing_signature": person.digital_signature if person else "",
            "view_url": reverse("notarius:view", args=[vf.pk]),
            "page_title": f"Sign — {contract.title or vf.title}",
        }

    def get(self, request, file_pk):
        vf = _get_file(request, file_pk)
        contract = _read(vf)
        person = _current_person(request)
        return render(request, self.template_name,
                      PageProcessor().decorate(self._ctx(request, vf, contract, person), request))

    def post(self, request, file_pk):
        from toto.gervazy.crypto import GervazyCryptoSession
        from toto.gervazy.models import UserStrongbox, WrappedDataKey
        from toto.gervazy.signing import SigningError, SigningService

        vf = _get_file(request, file_pk)
        contract = _read(vf)
        person = _current_person(request)

        if not person:
            messages.error(request, "You need a Person profile to sign.")
            return redirect("notarius:view", file_pk=vf.pk)

        party_id = request.POST.get("party_id", "").strip()
        password = request.POST.get("strongbox_password", "").strip()
        strongbox_id = request.POST.get("strongbox_id", "").strip()
        signature_data = request.POST.get("signature_data", "").strip()
        typed_name = request.POST.get("typed_name", "").strip()

        party = contract.party_by_id(party_id)
        if not party:
            messages.error(request, "Select which party you are signing as.")
            return render(request, self.template_name,
                          PageProcessor().decorate(self._ctx(request, vf, contract, person), request))
        if party_id not in _signable_party_ids(request, contract, vf):
            messages.error(request, "You can only sign as a party whose email matches your account.")
            return render(request, self.template_name,
                          PageProcessor().decorate(self._ctx(request, vf, contract, person), request))
        if not password:
            messages.error(request, "Strongbox password is required to sign.")
            return render(request, self.template_name,
                          PageProcessor().decorate(self._ctx(request, vf, contract, person), request))

        existing_key = SigningService.get_active_signing_key(person)
        if existing_key:
            signing_strongbox = existing_key.encrypted_private_key.strongbox
            wrapped_key = None
        else:
            try:
                signing_strongbox = (
                    UserStrongbox.objects.get(pk=strongbox_id, owner=request.user)
                    if strongbox_id else
                    UserStrongbox.objects.filter(owner=request.user).first()
                )
            except UserStrongbox.DoesNotExist:
                signing_strongbox = None
            if not signing_strongbox:
                messages.error(request, "No strongbox found. Set one up in Gervazy first.")
                return redirect("notarius:view", file_pk=vf.pk)
            wrapped_key = (
                WrappedDataKey.objects.filter(strongbox=signing_strongbox, state="active")
                .select_related("vmk").first()
            )
            if not wrapped_key:
                messages.error(request, f'No active data key in "{signing_strongbox.name}". '
                                        "Initialize one in Gervazy before signing.")
                return render(request, self.template_name,
                              PageProcessor().decorate(self._ctx(request, vf, contract, person), request))

        try:
            session = GervazyCryptoSession(signing_strongbox, password)
            signed_at = timezone.now()
            signed_at_iso = signed_at.isoformat(timespec="seconds")
            content_hash = contract.content_hash or contract.computed_content_hash()
            contract.content_hash = content_hash

            payload = SigningService.canonical_contract_file_payload(
                contract.id, contract.version, content_hash, party_id, signed_at_iso,
            )
            doc_sig = SigningService.sign_document(session, person, payload, wrapped_key=wrapped_key)
            session.close()
        except (SigningError, Exception) as exc:
            messages.error(request, f"Signing failed: {exc}")
            return render(request, self.template_name,
                          PageProcessor().decorate(self._ctx(request, vf, contract, person), request))

        # The pad posts a full data URL; store only the raw base64 in the XML image.
        image_b64 = signature_data.split(",", 1)[1] if signature_data.startswith("data:") else signature_data

        sig = contract_format.Signature(
            id=f"sig-{len(contract.signatures) + 1}",
            party=party_id,
            representative=(party.representative.id if party.representative else ""),
            target=contract.content.id,
            type="electronic", status="completed",
            signed_at=signed_at_iso, intent="approve-and-sign",
            appearance=contract_format.Appearance(typed_name=typed_name, image_b64=image_b64),
            method="ed25519", signed_hash=content_hash,
            signature_value=doc_sig.signature_b64, signature_algorithm="Ed25519",
            public_key_pem=doc_sig.public_key_pem,
        )
        contract_format.add_signature(contract, sig)
        contract_format.append_audit(contract, "signed", actor=party_id, signature=sig.id)

        if signature_data and not person.digital_signature:
            person.digital_signature = signature_data
            person.save(update_fields=["digital_signature"])

        _write(vf, contract)
        messages.success(request, "Contract signed — electronic signature recorded.")
        return redirect("notarius:view", file_pk=vf.pk)


# ---------------------------------------------------------------------------
# Convert to PDF (via the type's admin LaTeX template)
# ---------------------------------------------------------------------------


class ContractConvertPdfView(LoginRequiredMixin, View):
    """Render the contract to PDF on a Celery worker (sync fallback if none)."""

    def post(self, request, file_pk):
        vf = _get_file(request, file_pk, owner_only=True)
        from toto.notarius.models import ContractPdfJob
        from toto.notarius.tasks import render_contract_pdf_task

        job = ContractPdfJob.objects.create(vault_file=vf)

        dispatched = False
        try:
            from toto.celery_utils import celery_available
            if celery_available():
                render_contract_pdf_task.delay(job.pk)
                dispatched = True
        except Exception:
            pass

        if dispatched:
            messages.success(request, "Generating the PDF in the background — "
                                      "it'll appear in the vault shortly.")
            return redirect("notarius:view", file_pk=vf.pk)

        # No worker available — run it inline (eager) so it still works.
        render_contract_pdf_task.apply(args=[job.pk])
        job.refresh_from_db()
        if job.status == ContractPdfJob.Status.SUCCESS and job.pdf_vault_file:
            messages.success(request, f'Converted to PDF — saved as "{job.pdf_vault_file.title}" in the vault.')
        elif any(s in (job.log or "").lower() for s in ("no such file", "pdflatex", "filenotfound")):
            messages.error(request, "pdflatex is not installed on this deployment "
                                    "(PDF export needs the labs / TeX layer).")
        else:
            messages.error(request, f"PDF conversion failed: {(job.log or '')[:300]}")
        return redirect("notarius:view", file_pk=vf.pk)
