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
from django.views.decorators.http import require_GET, require_POST

from toto.ui import PageProcessor

from . import status as jess_status
from .forms import ComposeForm
from .models import EmailProvider, MailMessage

# The outbox is a diagnostic surface, not an archive browser. Same cap idea as
# fileservices' RUN_LIST_CAP.
OUTBOX_CAP = 200


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


def compose(request):
    """Send a message by hand — the test page.

    Goes through exactly the same path as every other email in the platform: Django's
    ``send_mail`` machinery, Jess's backend, a row, a task. A separate "test send" path
    would prove the test path works and nothing else.
    """
    _staff_only(request)

    form = ComposeForm(request.POST or None)
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
            headers={PURPOSE_HEADER: MailMessage.PURPOSE_TEST},
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
    })


def outbox(request):
    """What has been sent, and what has not."""
    _staff_only(request)
    rows = list(
        MailMessage.objects.select_related("provider")[:OUTBOX_CAP]
    )
    return _render(request, "jess/outbox.html", {
        "messages_list": rows,
        "page_title": "Outbox",
        "capped": len(rows) >= OUTBOX_CAP,
        "cap": OUTBOX_CAP,
        "delivery_status": jess_status.describe(),
        "compose_url": reverse("jess:compose"),
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
