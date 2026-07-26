from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from toto.socialhub.models import Community, Constitution, ConstitutionSignature
from toto.socialhub.permissions import current_person


def _render(request, template, context):
    from toto.ui import PageProcessor
    processor = PageProcessor()
    return render(request, template, processor.decorate(context, request))


def constitution_detail(request, slug):
    community = get_object_or_404(Community, slug=slug)
    constitution = get_object_or_404(Constitution, community=community, is_active=True)
    person = current_person(request)

    my_signature = None
    if person:
        my_signature = ConstitutionSignature.objects.filter(
            constitution=constitution, person=person
        ).first()

    is_member = bool(
        person and person.communities.filter(pk=community.pk).exists()
    )

    signatures = (
        constitution.signatures
        .filter(signed_at__isnull=False)
        .select_related("person")
        .order_by("signed_at")
    )

    return _render(request, "socialhub/constitution_detail.html", {
        "community": community,
        "constitution": constitution,
        "signatures": signatures,
        "my_signature": my_signature,
        "is_member": is_member,
        "person": person,
    })


def constitution_sign(request, slug):
    from toto.gervazy.models import UserStrongbox
    from toto.gervazy.signing import SigningService, SigningError
    from toto.gervazy.crypto import GervazyCryptoSession

    community = get_object_or_404(Community, slug=slug)
    constitution = get_object_or_404(Constitution, community=community, is_active=True)
    person = current_person(request)

    is_member = bool(
        person and person.communities.filter(pk=community.pk).exists()
    )

    strongboxes = []
    existing_signing_key = None
    if person and request.user.is_authenticated:
        strongboxes = list(UserStrongbox.objects.filter(owner=request.user))
        existing_signing_key = SigningService.get_active_signing_key(person)

    my_signature = None
    if person:
        my_signature = ConstitutionSignature.objects.filter(
            constitution=constitution, person=person
        ).first()

    def _ctx():
        required_strongbox = None
        if existing_signing_key:
            required_strongbox = existing_signing_key.encrypted_private_key.strongbox
        return {
            "community": community,
            "constitution": constitution,
            "person": person,
            "my_signature": my_signature,
            "is_member": is_member,
            "strongboxes": strongboxes,
            "existing_signing_key": existing_signing_key,
            "required_strongbox": required_strongbox,
            "existing_signature": person.digital_signature if person else "",
        }

    if request.method == "POST":
        if not person:
            messages.error(request, "You need a Person profile to sign the constitution.")
            return redirect("socialhub:constitution_detail", slug=slug)
        if not is_member:
            messages.error(request, "Only community members may sign the constitution.")
            return redirect("socialhub:constitution_detail", slug=slug)
        if my_signature and my_signature.has_signed:
            messages.info(request, "You have already signed this constitution.")
            return redirect("socialhub:constitution_detail", slug=slug)

        password = request.POST.get("strongbox_password", "").strip()
        strongbox_id = request.POST.get("strongbox_id", "").strip()
        signature_data = request.POST.get("signature_data", "").strip()

        if not password:
            messages.error(request, "Strongbox password is required to sign.")
            return _render(request, "socialhub/constitution_sign.html", _ctx())

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
                    return redirect("socialhub:constitution_detail", slug=slug)
            except UserStrongbox.DoesNotExist:
                messages.error(request, "Invalid strongbox selection.")
                return _render(request, "socialhub/constitution_sign.html", _ctx())

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
                    "Initialize a data key in Gervazy before signing.",
                )
                return _render(request, "socialhub/constitution_sign.html", _ctx())

        try:
            session = GervazyCryptoSession(signing_strongbox, password)

            signed_at = timezone.now()
            payload = SigningService.canonical_constitution_payload(constitution, person, signed_at)
            doc_sig = SigningService.sign_document(session, person, payload, wrapped_key=wrapped_key)

            sig, _ = ConstitutionSignature.objects.get_or_create(
                constitution=constitution,
                person=person,
            )
            sig.signed_at = signed_at
            sig.signature_data = signature_data
            sig.signing_payload = payload.decode("utf-8")
            sig.cryptographic_signature = doc_sig.signature_b64
            sig.signing_key = SigningService.get_active_signing_key(person).encrypted_private_key
            sig.save(update_fields=[
                "signed_at", "signature_data",
                "signing_payload", "cryptographic_signature", "signing_key",
            ])

            if signature_data and not person.digital_signature:
                person.digital_signature = signature_data
                person.save(update_fields=["digital_signature"])

            session.close()

        except Exception as exc:
            messages.error(request, f"Signing failed: {exc}")
            return _render(request, "socialhub/constitution_sign.html", _ctx())

        messages.success(request, "Constitution signed and cryptographic signature recorded.")
        return redirect("socialhub:constitution_detail", slug=slug)

    return _render(request, "socialhub/constitution_sign.html", _ctx())
