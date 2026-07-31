"""Jess's staff surfaces: compose, the outbox, and one message polled live.

**The gate is 403, not a redirect, and that is load-bearing.** The copy-pasted
``superuser_required = user_passes_test(...)`` decorator used by connectors, formica and
ocr returns **302 to LOGIN_URL for JSON endpoints too**. A poller then follows the
redirect, receives an HTML login page with status 200, ``res.json()`` throws, and the
house ``catch { keep polling }`` idiom loops forever against a page it will never parse.
Raising ``PermissionDenied`` gives 403, which the poller can act on. Same reasoning as
``quota/views.py:35-37`` and ``monit/views.py:35-42``.

The check is ``is_staff or is_superuser``: ``is_superuser`` does not imply ``is_staff``
in Django, which ``core/views.py:131-132`` states outright.

**There is deliberately no ``@login_required`` on any view here.** It would run BEFORE
``_staff_only`` and redirect an anonymous request to LOGIN_URL — reintroducing the exact
302 this module exists to avoid, and doing it on the JSON endpoint too. ``_staff_only``
already tests ``is_authenticated``, so anonymous gets the same honest 403 as a signed-in
non-staff user. Do not add one back.
"""
from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from toto.ui import PageProcessor

from . import delivery
from . import status as jess_status
from . import vault
from .forms import ComposeForm
from .models import EmailProvider, MailMessage

# The outbox is a diagnostic surface, not an archive browser. Same cap idea as
# fileservices' RUN_LIST_CAP.
OUTBOX_CAP = 200

# The most held messages one release request will send. A release is inline — the admin
# waits while it happens — so this bounds the request no matter how large the backlog
# grows, and the page reports "N released, M still held" so the rest is one more click.
RELEASE_BATCH_CAP = 25


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _staff_only(request):
    user = request.user
    if not (user.is_authenticated and (user.is_staff or user.is_superuser)):
        raise PermissionDenied


def _payload(message: MailMessage) -> dict:
    """What the poller reads. ``is_terminal`` is what tells it to stop."""
    return {
        "id": message.pk,
        "status": message.status,
        "status_display": message.get_status_display(),
        "is_terminal": message.is_terminal,
        "attempts": message.attempts,
        "error": message.error,
        "provider_label": message.provider_label,
        "queued_at": message.queued_at.isoformat() if message.queued_at else None,
        "started_at": message.started_at.isoformat() if message.started_at else None,
        "finished_at": message.finished_at.isoformat() if message.finished_at else None,
    }


@sensitive_post_parameters("passphrase")
def compose(request):
    """Send a message by hand.

    Goes through exactly the same path as every other email in the platform: Django's
    ``send_mail`` machinery, Jess's backend, a row, a task (or, in manual mode, a held
    row). A separate send path would prove that path works and nothing else.

    In manual-release mode the message is recorded HELD like any other. If the admin
    also types the passphrase, it is released inline right away — composing is itself an
    admin-with-passphrase-present action, so there is no reason to make them visit the
    release page for their own message.
    """
    _staff_only(request)
    manual = vault.manual_release_enabled()

    form = ComposeForm(request.POST or None, manual=manual)
    if request.method == "POST" and form.is_valid():
        from django.core.mail import EmailMultiAlternatives, get_connection

        from .backend import PURPOSE_HEADER

        # An explicit connection so the row this send wrote can be identified exactly.
        # Querying for "the newest row that looks like mine" would be a race.
        connection = get_connection()

        email = EmailMultiAlternatives(
            subject=form.cleaned_data["subject"],
            body=form.cleaned_data["body"],
            to=form.cleaned_data["to"],
            headers={PURPOSE_HEADER: MailMessage.PURPOSE_MANUAL},
            connection=connection,
        )
        if form.cleaned_data["html_body"]:
            email.attach_alternative(form.cleaned_data["html_body"], "text/html")
        email.send(fail_silently=False)

        # recorded_ids only exists on Jess's own backend. A host that has jess installed
        # but EMAIL_BACKEND pointed elsewhere still sends — it just has no outbox row to
        # link to, which is worth saying rather than 500ing on a missing attribute.
        recorded = list(getattr(connection, "recorded_ids", []))
        if recorded:
            # Claimed for the acting user here rather than in the backend, which has no
            # request and also serves password reset.
            MailMessage.objects.filter(pk__in=recorded).update(created_by=request.user)

            passphrase = (request.POST.get("passphrase") or "").strip()
            if manual and passphrase:
                error = _release_ids(request, recorded, passphrase)
                if error:
                    messages.error(request, error)
            return redirect(reverse("jess:message_detail", args=[recorded[0]]))

        messages.warning(
            request,
            "The message was handed to Django, but EMAIL_BACKEND is not Jess — so there "
            "is no outbox row for it. Set EMAIL_BACKEND to "
            "'toto.jess.backend.JessEmailBackend' to record and track sends.",
        )
        return redirect(reverse("jess:outbox"))

    return _render(request, "jess/compose.html", {
        "form": form,
        "page_title": "Send a message",
        "delivery_status": jess_status.describe(),
        "can_deliver": jess_status.can_deliver(),
        "provider": EmailProvider.active_provider(),
        "manual": manual,
    })


def outbox(request):
    """What has been sent, and what has not."""
    _staff_only(request)
    rows = list(
        MailMessage.objects.select_related("provider")[:OUTBOX_CAP]
    )
    # Counted across the whole table, not just the capped page. This is the visible face
    # of the cost Jess accepts by queueing everything: on a host with no celery worker,
    # mail is accepted and never leaves — and the only symptom is a growing pile of rows
    # that never reach a terminal status. Nothing else in the platform would say so.
    stuck = MailMessage.objects.filter(
        status__in=[MailMessage.QUEUED, MailMessage.SENDING]
    ).count()
    held = MailMessage.objects.filter(status=MailMessage.HELD).count()
    return _render(request, "jess/outbox.html", {
        "messages_list": rows,
        "page_title": "Outbox",
        "capped": len(rows) >= OUTBOX_CAP,
        "cap": OUTBOX_CAP,
        "waiting": stuck,
        "held": held,
        "manual": vault.manual_release_enabled(),
        "delivery_status": jess_status.describe(),
        "compose_url": reverse("jess:compose"),
        "release_url": reverse("jess:release"),
    })


def message_detail(request, pk):
    """One message, polled until it reaches a terminal status."""
    _staff_only(request)
    message = get_object_or_404(MailMessage, pk=pk)
    return _render(request, "jess/message_detail.html", {
        "message": message,
        "page_title": "Message",
        "payload": _payload(message),
        "status_url": reverse("jess:message_status", args=[message.pk]),
        "retry_url": reverse("jess:message_retry", args=[message.pk]),
        "release_url": reverse("jess:release"),
        "is_held": message.status == MailMessage.HELD,
    })


@require_GET
def message_status(request, pk):
    """The polled JSON. Staff-gated to 403 — see the module docstring."""
    _staff_only(request)
    message = get_object_or_404(MailMessage, pk=pk)
    return JsonResponse(_payload(message))


@require_POST
def message_retry(request, pk):
    """Re-dispatch a failed message.

    Explicit, human, one attempt at a time — Jess has no automatic retries, because a
    silent backoff hides a misconfigured server for hours. ``attempts`` increments so a
    person repeatedly pressing the button is visible.
    """
    _staff_only(request)
    message = get_object_or_404(MailMessage, pk=pk)

    if message.status == MailMessage.SENDING:
        messages.info(request, "That message is already being sent.")
        return redirect(reverse("jess:message_detail", args=[message.pk]))

    from django.utils import timezone

    # In manual-release mode there is no worker to dispatch to and no ambient passphrase
    # to send with, so retry means "put it back in the held pile" — the admin re-releases
    # it with the passphrase. Dispatching to Celery would leave it QUEUED forever.
    if vault.manual_release_enabled():
        MailMessage.objects.filter(pk=message.pk).update(
            status=MailMessage.HELD, error="", finished_at=None, started_at=None,
        )
        messages.success(request, "Returned to held — release it with the passphrase.")
        return redirect(reverse("jess:message_detail", args=[message.pk]))

    MailMessage.objects.filter(pk=message.pk).update(
        status=MailMessage.QUEUED, error="", finished_at=None, started_at=None,
        queued_at=timezone.now(),
    )

    from .backend import JessEmailBackend

    # Reuse the backend's dispatch so a broker failure is recorded on the row the same
    # way it is for a first attempt, rather than 500ing this POST.
    JessEmailBackend()._dispatch(message)
    messages.success(request, "Queued again.")
    return redirect(reverse("jess:message_detail", args=[message.pk]))


# -- manual release: the passphrase never outlives the request -------------------


def _release_ids(request, ids, passphrase: str):
    """Send the HELD rows with these ids, inline, under an admin-typed passphrase.

    The passphrase lives only as this argument, for this request. One connection is
    built and reused across the whole batch. Returns ``None`` on success (a summary
    message is posted) or an error string if the passphrase did not open the vault — in
    which case NOTHING was sent and nothing changed, so the operator can simply retype it.
    """
    rows = list(
        MailMessage.objects.filter(pk__in=ids, status=MailMessage.HELD).order_by("queued_at")
    )
    if not rows:
        return None
    try:
        session = vault.open_manual_session(passphrase)
        provider = delivery.resolve_provider(rows[0])
        # Building the connection reads the SMTP secret through the typed session, so a
        # wrong passphrase raises HERE, before any row is touched.
        connection = delivery.build_connection(provider, session=session)
    except vault.VaultUnavailable as exc:
        return f"{exc} Nothing was sent, and nothing is locked out — try the passphrase again."
    except delivery.NoProvider as exc:
        return str(exc)

    sent = failed = 0
    try:
        for row in rows:
            if delivery.release_message(row, session=session, connection=connection,
                                        released_by=request.user):
                sent += 1
            else:
                failed += 1
    finally:
        try:
            connection.close()
        except Exception:                       # noqa: BLE001 — best-effort cleanup
            pass
        session.close()

    remaining = MailMessage.objects.filter(status=MailMessage.HELD).count()
    note = f"Released {sent} message(s)"
    if failed:
        note += f"; {failed} failed (see the outbox for why)"
    note += f". {remaining} still held." if remaining else "."
    messages.success(request, note)
    return None


@sensitive_post_parameters("passphrase")
def release(request):
    """Release held mail: type the passphrase, Jess sends a batch, the passphrase is
    dropped when the request ends. This page never renders a message body — a held
    password-reset link is released without anyone reading it.
    """
    _staff_only(request)
    held_qs = MailMessage.objects.filter(status=MailMessage.HELD).order_by("queued_at")
    context = {
        "page_title": "Release held mail",
        "held": list(held_qs[:RELEASE_BATCH_CAP]),
        "held_count": held_qs.count(),
        "cap": RELEASE_BATCH_CAP,
        "manual": vault.manual_release_enabled(),
        "delivery_status": jess_status.describe(),
        "error": None,
    }
    if request.method != "POST":
        return _render(request, "jess/release.html", context)

    passphrase = (request.POST.get("passphrase") or "").strip()
    single_pk = request.POST.get("pk")
    if not passphrase:
        context["error"] = "Type the vault passphrase to release."
        return _render(request, "jess/release.html", context)

    # A single message released from its detail page.
    if single_pk:
        ids = list(
            MailMessage.objects.filter(pk=single_pk, status=MailMessage.HELD)
            .values_list("pk", flat=True)
        )
        if not ids:
            messages.info(request, "That message is no longer held.")
        else:
            error = _release_ids(request, ids, passphrase)
            if error:
                messages.error(request, error)
        return redirect(reverse("jess:message_detail", args=[single_pk]))

    # The oldest batch, bounded by the cap.
    ids = list(held_qs.values_list("pk", flat=True)[:RELEASE_BATCH_CAP])
    if not ids:
        messages.info(request, "Nothing is held.")
        return redirect(reverse("jess:outbox"))
    error = _release_ids(request, ids, passphrase)
    if error:
        context["error"] = error
        return _render(request, "jess/release.html", context)
    return redirect(reverse("jess:outbox"))


@sensitive_post_parameters("passphrase", "passphrase2", "smtp_password")
def vault_setup(request):
    """First-time setup: an admin CHOOSES the passphrase, which creates the strongbox.

    This is how a manual-release host initialises its vault, since ``deploy.py`` no
    longer mints a passphrase into the environment there. Optionally stores the first
    SMTP password in the same transaction so the whole thing is set up in one step.
    """
    _staff_only(request)
    if vault.system_strongbox() is not None:
        messages.info(request, "The mail vault is already set up.")
        return redirect(reverse("jess:outbox"))

    context = {
        "page_title": "Set up the mail vault",
        "error": None,
        "providers": list(EmailProvider.objects.all()),
    }
    if request.method != "POST":
        return _render(request, "jess/vault_setup.html", context)

    p1 = request.POST.get("passphrase") or ""
    p2 = request.POST.get("passphrase2") or ""
    if not p1:
        context["error"] = "Choose a passphrase."
        return _render(request, "jess/vault_setup.html", context)
    if p1 != p2:
        context["error"] = "The two passphrases do not match."
        return _render(request, "jess/vault_setup.html", context)

    from django.db import transaction

    from toto.gervazy.crypto import GervazyCryptoSession

    smtp_password = (request.POST.get("smtp_password") or "").strip()
    provider = EmailProvider.objects.filter(pk=request.POST.get("provider")).first()
    try:
        with transaction.atomic():
            owner = vault._get_or_create_owner()
            session, _dek = GervazyCryptoSession.initialize_strongbox(
                owner, vault.SYSTEM_STRONGBOX_NAME, p1,
            )
            if smtp_password and provider is not None:
                secret = vault.store_secret(
                    smtp_password,
                    name=vault.unique_secret_name(f"jess-{provider.pk}"),
                    session=session,
                )
                provider.secret = secret
                provider.save(update_fields=["secret"])
    except Exception as exc:                    # noqa: BLE001 — surface, never echo values
        context["error"] = f"Could not set up the vault: {exc}"
        return _render(request, "jess/vault_setup.html", context)

    vault.clear_cache()
    messages.success(
        request,
        "The mail vault is set up. Keep the passphrase somewhere safe — it is stored "
        "nowhere on the server and cannot be recovered.",
    )
    return redirect(reverse("jess:outbox"))


@sensitive_post_parameters("passphrase", "smtp_password")
def provider_secret(request):
    """Set or replace a provider's SMTP password, gated on the typed passphrase."""
    _staff_only(request)
    context = {
        "page_title": "Set the SMTP password",
        "error": None,
        "providers": list(EmailProvider.objects.all()),
    }
    if request.method != "POST":
        return _render(request, "jess/provider_secret.html", context)

    passphrase = request.POST.get("passphrase") or ""
    new_password = (request.POST.get("smtp_password") or "").strip()
    provider = EmailProvider.objects.filter(pk=request.POST.get("provider")).first()
    if provider is None:
        context["error"] = "Choose a provider."
        return _render(request, "jess/provider_secret.html", context)
    if not new_password:
        context["error"] = "Enter the SMTP password to store."
        return _render(request, "jess/provider_secret.html", context)

    try:
        session = vault.open_manual_session(passphrase)
        old = provider.secret
        # Prove the passphrase against the existing secret before replacing it. (Storing
        # would fail on a wrong passphrase anyway — it cannot unwrap the VMK — but this
        # gives a clear message rather than a generic encrypt error.)
        if old is not None:
            vault.read_secret(old, session=session)
        secret = vault.store_secret(
            new_password,
            name=vault.unique_secret_name(f"jess-{provider.pk}"),
            session=session,
        )
        provider.secret = secret
        provider.save(update_fields=["secret"])
        vault.retire_secret(old)
        vault.log_secret_event(
            request.user, "set_email_password", secret,
            reason=f"set via the release console for provider #{provider.pk}",
        )
    except vault.VaultUnavailable as exc:
        context["error"] = f"{exc} The password was NOT changed."
        return _render(request, "jess/provider_secret.html", context)
    except Exception as exc:                    # noqa: BLE001 — never echo the value
        context["error"] = f"Could not store the password: {exc}"
        return _render(request, "jess/provider_secret.html", context)

    messages.success(request, f"SMTP password stored for '{provider.label}', encrypted.")
    return redirect(reverse("jess:outbox"))


@sensitive_post_parameters("old_passphrase", "new_passphrase", "new_passphrase2")
def vault_rotate_passphrase(request):
    """Change the passphrase that unlocks the vault. Re-wraps the key hierarchy only —
    no stored SMTP password is re-encrypted or exposed.
    """
    _staff_only(request)
    context = {"page_title": "Change the vault passphrase", "error": None}
    if request.method != "POST":
        return _render(request, "jess/rotate_passphrase.html", context)

    old = request.POST.get("old_passphrase") or ""
    new1 = request.POST.get("new_passphrase") or ""
    new2 = request.POST.get("new_passphrase2") or ""
    if not new1:
        context["error"] = "Enter a new passphrase."
        return _render(request, "jess/rotate_passphrase.html", context)
    if new1 != new2:
        context["error"] = "The two new passphrases do not match."
        return _render(request, "jess/rotate_passphrase.html", context)

    try:
        vault.rotate_passphrase(old, new1, actor=request.user)
    except Exception as exc:                    # noqa: BLE001 — wrong old passphrase, etc.
        context["error"] = str(exc)
        return _render(request, "jess/rotate_passphrase.html", context)

    messages.success(
        request,
        "Vault passphrase changed. Use the new one from now on — the old one no longer "
        "works, and no stored password had to change.",
    )
    return redirect(reverse("jess:outbox"))
