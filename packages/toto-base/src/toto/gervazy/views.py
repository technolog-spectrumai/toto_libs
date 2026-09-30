import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

log = logging.getLogger("toto.gervazy")


@login_required
def my_keys(request):
    from toto.gervazy.models import UserStrongbox, PersonSigningKey
    from toto.people.models import Person

    strongboxes = list(
        UserStrongbox.objects
        .filter(owner=request.user)
        .prefetch_related("data_keys", "master_keys")
        .order_by("name")
    )

    try:
        person = Person.objects.get(user=request.user)
    except Person.DoesNotExist:
        person = None

    signing_keys = []
    if person:
        signing_keys = list(
            PersonSigningKey.objects
            .filter(person=person)
            .select_related(
                "encrypted_private_key__wrapped_key__vmk__strongbox",
            )
            .order_by("-created_at")
        )

    context = {
        "strongboxes": strongboxes,
        "signing_keys": signing_keys,
        "person": person,
    }
    return render(request, "gervazy/my_keys.html", PageProcessor().decorate(context, request))


@login_required
@require_POST
def initialize_strongbox_view(request, pk):
    """
    Provision a VMK + DEK for a strongbox that was created without one.
    If the strongbox already has an active VMK, only a new DEK is created.
    """
    from toto.gervazy.models import UserStrongbox, VaultMasterKey, WrappedDataKey
    from toto.gervazy.crypto import (
        GervazyCryptoSession,
        decode_derived_key,
        generate_raw_key,
        aes_gcm_encrypt,
    )

    strongbox = get_object_or_404(UserStrongbox, pk=pk, owner=request.user)
    password = request.POST.get("password", "").strip()

    if not password:
        messages.error(request, _("Password is required to initialize the strongbox."))
        return redirect("gervazy:my_keys")

    try:
        raw_ukek = decode_derived_key(strongbox.derive_key(password))

        vmk = VaultMasterKey.objects.filter(strongbox=strongbox, state="active").first()

        if vmk is None:
            raw_vmk = generate_raw_key()
            encrypted_vmk, vmk_nonce = aes_gcm_encrypt(raw_ukek, raw_vmk)
            next_version = 1 + (
                VaultMasterKey.objects.filter(strongbox=strongbox).order_by("-version").values_list("version", flat=True).first() or 0
            )
            vmk = VaultMasterKey.objects.create(
                strongbox=strongbox,
                encrypted_vmk=encrypted_vmk,
                nonce=vmk_nonce,
                version=next_version,
                state="active",
            )
        else:
            # Verify password decrypts the existing VMK correctly before adding a DEK.
            session = GervazyCryptoSession(strongbox, password)
            raw_vmk = session._unwrap_vmk(vmk)

        raw_dek = generate_raw_key()
        encrypted_dek, dek_nonce = aes_gcm_encrypt(raw_vmk, raw_dek)
        next_dek_version = 1 + (
            WrappedDataKey.objects.filter(strongbox=strongbox).order_by("-version").values_list("version", flat=True).first() or 0
        )
        WrappedDataKey.objects.create(
            strongbox=strongbox,
            vmk=vmk,
            encrypted_dek=encrypted_dek,
            nonce=dek_nonce,
            version=next_dek_version,
            state="active",
        )

        messages.success(request, _("Strongbox \"%(name)s\" initialized successfully.")
                         % {"name": strongbox.name})

    except Exception as exc:  # noqa: BLE001
        # A sentence, never the exception's text (review, 2026-10-01): what
        # the crypto layer says is not the member's to read. The class is
        # enough to find it in the log.
        log.warning("gervazy: strongbox %s not initialised (%s)", strongbox.pk,
                    type(exc).__name__)
        messages.error(request, _("Initialization failed: the password is wrong, or "
                                  "the strongbox could not be opened."))

    return redirect("gervazy:my_keys")


@login_required
@require_POST
def provision_signing_key_view(request):
    """
    Provision a new Ed25519 signing key for the current person.
    Uses the selected strongbox and password.
    """
    from toto.gervazy.models import UserStrongbox, WrappedDataKey
    from toto.gervazy.crypto import GervazyCryptoSession
    from toto.gervazy.signing import SigningService
    from toto.people.models import Person

    try:
        person = Person.objects.get(user=request.user)
    except Person.DoesNotExist:
        messages.error(request, _("No Person profile linked to your account."))
        return redirect("gervazy:my_keys")

    strongbox_id = request.POST.get("strongbox_id", "").strip()
    password = request.POST.get("password", "").strip()

    if not password:
        messages.error(request, _("Password is required."))
        return redirect("gervazy:my_keys")

    try:
        if strongbox_id:
            strongbox = get_object_or_404(UserStrongbox, pk=strongbox_id, owner=request.user)
        else:
            strongbox = UserStrongbox.objects.filter(owner=request.user).first()
            if not strongbox:
                messages.error(request, _("No strongbox found."))
                return redirect("gervazy:my_keys")

        wrapped_key = (
            WrappedDataKey.objects
            .filter(strongbox=strongbox, state="active")
            .select_related("vmk")
            .first()
        )
        if not wrapped_key:
            messages.error(request, _("Strongbox \"%(name)s\" has no active data key. "
                                     "Initialize it first.") % {"name": strongbox.name})
            return redirect("gervazy:my_keys")

        session = GervazyCryptoSession(strongbox, password)
        SigningService.provision_signing_key(session, wrapped_key, person)
        session.close()

        messages.success(request, _("New signing key provisioned using \"%(name)s\".")
                         % {"name": strongbox.name})

    except Http404:
        raise
    except Exception as exc:  # noqa: BLE001
        # As above: a sentence for the member, the class for the log.
        log.warning("gervazy: signing key not provisioned for account %s (%s)",
                    request.user.pk, type(exc).__name__)
        messages.error(request, _("Provisioning failed: the password is wrong, or the "
                                  "strongbox could not be opened."))

    return redirect("gervazy:my_keys")
