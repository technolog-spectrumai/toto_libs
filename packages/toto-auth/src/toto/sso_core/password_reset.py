"""Password reset, shared by both federated modes — two mutually exclusive flows.

It used to live in ``sso_master``, which meant only a *provider* host had it: a
consumer mounted ``sso_client.urls``, ``sso:password_reset`` did not reverse, and
``toto.core.auth_views._password_reset_url`` correctly hid the link. That was
right while a consumer had no local accounts; it stopped being right when
consumers grew accounts of their own. So it lives in ``sso_core``, the one auth
app installed in **both** federated modes, and both urlconfs mount the same
names. Local mode (``TOTO_AUTH_MODE=local``) still has no reset: it installs
neither ``sso_core`` nor any OIDC app.

## Flow 1 — email, when the platform can actually send one

The standard Django flow: email address in, single-use ``uidb64/token`` link
out. "Can actually send" has two answers, asked in order:

* the configured backend delivers (``email_delivery_configured()`` — a real
  SMTP config, a locmem test override, a Jess provider with a stored or
  manually-released secret): ``PasswordResetForm.save()`` exactly as before;
* **inline** — Jess's session custody (``jess.credentials``): no stored secret
  anywhere, but THIS web process holds the password a staff member typed (or
  the env bootstrap). The mail is then sent in this request rather than
  queued, because the Celery worker that would dequeue it cannot see this
  process's memory. Same template, same token, one outbox row.

## Flow 2 — patron-authorized recovery, when it cannot

No email leaves the platform at all. The user names their account; a recovery
ticket appears on their patron's own profile (see ``recovery.py`` for who
counts as the patron); the patron approves and is shown — once — a one-time
UUID link to hand over securely; the link is a new-password form; using it
burns it. The patron never sees nor chooses the password: they carry a link,
and the form at the end of it talks only to the user holding it.

Which flow serves a request is decided per request, silently: the same
``/password-reset/`` page renders the email form or the username form. The
choice depends only on platform configuration, never on the account named, so
it leaks nothing about anybody.

Two properties carried over from the old module, still load-bearing:

* **Reset only ever reaches a local account.** ``PasswordResetForm.get_users``
  filters on ``has_usable_password()``; ``recovery.file_request`` applies the
  same test. A federated identity is told the usual generic thing and nothing
  happens, which is the standard non-enumerating behaviour.
* **Generic responses everywhere.** The done page and the request page say the
  same words for a known and an unknown account; only the audit chain knows.

A password set at the end of either flow is ``AUTH.PASSWORD_RESET`` on the
chain (2026-09-30), with the flow — never the link.
"""
from django.apps import apps as django_apps
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sites.shortcuts import get_current_site
from django.db import transaction
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.template import loader
from django.urls import NoReverseMatch, reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.translation import gettext as _

from toto.core.auth_cooldown import (
    reset_request_cooldown_remaining,
    start_reset_request_cooldown,
)
from toto.core.email_config import email_delivery_configured
from toto.ui import PageProcessor

from . import recovery
from .models import RecoveryTicket, link_ttl


def _email_send_mode():
    """How a reset email would leave right now: "backend", "inline" or None.

    None means flow 2. Never raises — this runs while rendering pages for
    anonymous users, possibly before Jess's tables exist.
    """
    if email_delivery_configured():
        return "backend"
    if django_apps.is_installed("toto.jess"):
        try:
            from toto.jess.status import can_send_inline

            if can_send_inline():
                return "inline"
        except Exception:
            pass
    return None


def _send_inline(form: PasswordResetForm, request) -> None:
    """Flow 1's inline half: Django's own reset loop, sent on the in-memory
    credential instead of through the configured (non-delivering) backend.

    Mirrors ``PasswordResetForm.save()`` — same users, same token, same
    templates — so the confirm view downstream cannot tell the difference.
    A failed send is recorded on the outbox row by ``send_single_now`` and
    deliberately not surfaced here: the response stays generic.

    Known and accepted: sending in-request means a matching account costs an
    SMTP round-trip and a miss does not, so response TIMING can distinguish
    them. Django's own synchronous SMTP backend has the same property; the
    body of the response never differs, and the codebase's rule against
    third-party latency on anonymous hot paths (see ``jess.status``) is
    knowingly traded here for the no-persisted-credential custody.
    """
    from toto.jess import credentials
    from toto.jess.delivery import send_single_now
    from toto.jess.models import MailMessage

    password = credentials.credential()
    email = form.cleaned_data["email"]
    site = get_current_site(request)
    for user in form.get_users(email):
        context = {
            "email": user.email,
            "domain": site.domain,
            "site_name": site.name,
            "uid": urlsafe_base64_encode(force_bytes(user.pk)),
            "user": user,
            "token": default_token_generator.make_token(user),
            "protocol": "https" if request.is_secure() else "http",
        }
        subject = loader.render_to_string("sso/password_reset_subject.txt", context)
        subject = "".join(subject.splitlines())
        if password is None:
            # Unlocked away between the page render and this POST (a logout,
            # an expiry, a restart). Silence here would tell the user "check
            # your email" and tell staff nothing at all — so leave the same
            # trace a refused SMTP conversation would: a FAILED outbox row.
            MailMessage.objects.create(
                to=[user.email], subject=subject,
                purpose=MailMessage.PURPOSE_PASSWORD_RESET,
                status=MailMessage.FAILED,
                error="No in-process email credential at send time — "
                      "see /jess/unlock/.",
            )
            continue
        body = loader.render_to_string("sso/password_reset_email.html", context)
        send_single_now(
            subject=subject, body=body, to=[user.email],
            purpose=MailMessage.PURPOSE_PASSWORD_RESET, password=password,
        )


def _reset_on_chain(user, request, flow):
    """``AUTH.PASSWORD_RESET`` for a password set through a link (2026-09-30),
    and the "your password was changed" notice to the account's address
    (review, 2026-10-01): a reset is the change a member most needs to hear
    about when it was not theirs. ``send_notice`` never raises.

    Soft edge like ``_email_send_mode``: ``toto.audit`` is optional here, and
    ``on_password_reset`` already swallows a record it cannot write.
    """
    from toto.core.client_ip import client_ip
    from toto.core.notices import send_notice

    send_notice(user, "password_changed", {"address": client_ip(request)})
    if not django_apps.is_installed("toto.audit"):
        return None
    from toto.audit.identity import on_password_reset

    return on_password_reset(user, flow=flow, request=request)


def password_reset_view(request):
    processor = PageProcessor()
    mode = _email_send_mode()

    # ---- flow 2: no email path; the username form files a patron ticket ----
    if mode is None:
        context = {"flow": "ticket", "page_title": "Account Recovery",
                   "link_ttl_hours": int(link_ttl().total_seconds() // 3600),
                   "error": None}
        if request.method == "POST":
            if "username" not in request.POST and "email" in request.POST:
                # The client was shown the EMAIL form — the platform lost its
                # send path between their page load and this POST (a lock, an
                # expiry, a worker restart). Faking the ticket-filed page
                # would be a false success twice over: no ticket exists and
                # no mail was sent. Say what changed and re-offer the form
                # that works now. Flow choice is global configuration, so
                # this reveals nothing about any account.
                context["error"] = _(
                    "This site can no longer send reset emails — enter your "
                    "username below to ask for patron-approved recovery "
                    "instead."
                )
                return render(request, "sso/password_reset.html",
                              processor.decorate(context, request))
            remaining = reset_request_cooldown_remaining(request)
            if remaining > 0:
                context["error"] = _(
                    "Please wait %(seconds)s seconds before trying again."
                ) % {"seconds": remaining}
            else:
                start_reset_request_cooldown(request)
                recovery.file_request(request.POST.get("username"),
                                      request=request)
                # The same redirect whether the account exists or not.
                return redirect(
                    reverse("sso:password_reset_done") + "?flow=ticket"
                )
        return render(request, "sso/password_reset.html",
                      processor.decorate(context, request))

    # ---- flow 1: the email form -------------------------------------------
    form = PasswordResetForm(request.POST or None)
    context = {"flow": "email", "form": form, "page_title": "Reset Password",
               "error": None}

    if request.method == "POST" and form.is_valid():
        remaining = reset_request_cooldown_remaining(request)
        if remaining > 0:
            context["error"] = _(
                "Please wait %(seconds)s seconds before trying again."
            ) % {"seconds": remaining}
        else:
            start_reset_request_cooldown(request)
            if mode == "inline":
                _send_inline(form, request)
            else:
                form.save(
                    request=request,
                    use_https=request.is_secure(),
                    email_template_name="sso/password_reset_email.html",
                    subject_template_name="sso/password_reset_subject.txt",
                    extra_email_context=None,
                )
            return redirect(reverse("sso:password_reset_done"))

    return render(request, "sso/password_reset.html",
                  processor.decorate(context, request))


def password_reset_done_view(request):
    processor = PageProcessor()
    flow = "ticket" if request.GET.get("flow") == "ticket" else "email"
    context = {"flow": flow,
               "link_ttl_hours": int(link_ttl().total_seconds() // 3600),
               "page_title": "Check Your Email"
               if flow == "email" else "Recovery Requested"}
    return render(request, "sso/password_reset_done.html",
                  processor.decorate(context, request))


def password_reset_confirm_view(request, uidb64, token):
    processor = PageProcessor()
    User = get_user_model()

    user = None
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        pass

    valid = user is not None and default_token_generator.check_token(user, token)
    form = SetPasswordForm(user, request.POST or None) if valid else None
    context = {"form": form, "validlink": valid, "page_title": "Set New Password"}

    if request.method == "POST" and valid and form.is_valid():
        form.save()
        _reset_on_chain(user, request, "email")
        return redirect(reverse("sso:password_reset_complete"))

    return render(request, "sso/password_reset_confirm.html",
                  processor.decorate(context, request))


def password_reset_complete_view(request):
    processor = PageProcessor()
    context = {"page_title": "Password Reset Complete"}
    return render(request, "sso/password_reset_complete.html",
                  processor.decorate(context, request))


# --- flow 2's three extra pages ----------------------------------------------

def password_reset_recover_view(request, token):
    """The one-time link a patron handed over: a new-password form, then gone.

    Renders the same template as the email flow's confirm page — the user
    experience of "I followed my reset link" is one thing, however the link
    reached them. Burn-then-save, in one transaction: the row lock in
    ``mark_used`` is what makes the link single-use under concurrency, and the
    save joining its transaction means a burned link always means a changed
    password.
    """
    processor = PageProcessor()
    ticket = recovery.redeem_ticket(token)
    valid = ticket is not None
    form = SetPasswordForm(ticket.user, request.POST or None) if valid else None
    context = {"form": form, "validlink": valid, "page_title": "Set New Password"}

    if request.method == "POST" and valid and form.is_valid():
        with transaction.atomic():
            if recovery.mark_used(ticket, request=request):
                form.save()
                _reset_on_chain(ticket.user, request, "recovery")
                return redirect(reverse("sso:password_reset_complete"))
        context.update({"form": None, "validlink": False})

    return render(request, "sso/password_reset_confirm.html",
                  processor.decorate(context, request))


def _back_to_profile(request):
    """Where an approver lands after acting on a card: their own profile,
    where the card was — or the dashboard when profiles are not mounted."""
    if django_apps.is_installed("toto.people"):
        from toto.people.models import Person

        person = Person.objects.filter(user=request.user).only("slug").first()
        if person is not None and person.slug:
            try:
                return reverse("socialhub:profile_details", args=[person.slug])
            except NoReverseMatch:
                pass
    try:
        return reverse("core:dashboard")
    except NoReverseMatch:                      # pragma: no cover
        return "/"


def _respond_gate(request, ticket):
    """The shared door for approve/reject. Returns a redirect, or None to
    proceed.

    Not ``@require_POST``: an approver whose session expired POSTs, logs back
    in, and the login redirect replays the URL as a GET — a 405 there is a
    dead end where a bounce to the profile (where the card still is) reads as
    "try again". And a second staff member racing the same queue card loses
    the ``may_respond`` check the moment the winner claims it, so a ticket
    that is simply no longer pending answers a responder-shaped viewer with
    the friendly bounce rather than a 403; a true stranger still gets the
    403, whatever the ticket's state.
    """
    could_respond = recovery.may_respond(request.user, ticket) or (
        ticket.status != RecoveryTicket.PENDING
        and ticket.user_id != request.user.pk
        and (request.user.is_staff or request.user.is_superuser)
    )
    if not could_respond:
        raise PermissionDenied
    if request.method != "POST" or ticket.status != RecoveryTicket.PENDING:
        if ticket.status != RecoveryTicket.PENDING:
            messages.info(request,
                          _("That recovery ticket is no longer pending."))
        return redirect(_back_to_profile(request))
    return None


@login_required
def recovery_ticket_approve(request, pk):
    """Approve a recovery card: mint the link, show it ONCE.

    The response page is the only place the link ever exists server-side, and
    only for this render — the row keeps a hash. A refresh re-POSTs, finds the
    ticket no longer pending, and bounces back to the profile; a lost link is
    recovered by the user simply requesting again.
    """
    processor = PageProcessor()
    ticket = get_object_or_404(RecoveryTicket, pk=pk)
    bounce = _respond_gate(request, ticket)
    if bounce is not None:
        return bounce
    token = recovery.approve(ticket, request.user)
    if token is None:
        messages.info(request, _("That recovery ticket is no longer pending."))
        return redirect(_back_to_profile(request))
    link = request.build_absolute_uri(
        reverse("sso:password_reset_recover", args=[token])
    )
    context = {
        "ticket": ticket,
        "link": link,
        "link_ttl_hours": int(link_ttl().total_seconds() // 3600),
        "back_url": _back_to_profile(request),
        "page_title": "Recovery Link",
    }
    return render(request, "sso/recovery_link.html",
                  processor.decorate(context, request))


@login_required
def recovery_ticket_reject(request, pk):
    ticket = get_object_or_404(RecoveryTicket, pk=pk)
    bounce = _respond_gate(request, ticket)
    if bounce is not None:
        return bounce
    if recovery.reject(ticket, request.user):
        messages.success(request, _("Recovery ticket rejected."))
    else:
        messages.info(request, _("That recovery ticket is no longer pending."))
    return redirect(_back_to_profile(request))


def urlpatterns(prefix: str = ""):
    """The reset routes, for whichever urlconf serves the ``sso`` namespace.

    A function rather than a module-level list because the two urlconfs are
    mounted at different depths and must still produce the same *names*:
    ``sso_master.urls`` is included at ``""`` and carries its own ``sso/``
    segment, while ``sso_client.urls`` is included at ``"sso/"`` and must not
    repeat it. Pass the prefix the caller needs; the names are defined once
    here, so they cannot drift between provider and consumer.
    """
    from django.urls import path

    base = f"{prefix}password-reset/"
    return [
        path(base, password_reset_view, name="password_reset"),
        path(f"{base}done/", password_reset_done_view, name="password_reset_done"),
        path(f"{base}complete/", password_reset_complete_view,
             name="password_reset_complete"),
        # Flow 2: the one-time link a patron hands over, and the two actions
        # on the ticket card (the card renders on the approver's own profile
        # via sso_core/plugins/profile_plugins.py). These MUST precede the
        # two-segment confirm route below: "recover/<uuid>" and
        # "ticket/<uuid>" both parse as a perfectly good <uidb64>/<token>
        # pair, and Django takes the first match.
        path(f"{base}recover/<uuid:token>/", password_reset_recover_view,
             name="password_reset_recover"),
        path(f"{base}ticket/<uuid:pk>/approve/", recovery_ticket_approve,
             name="password_reset_ticket_approve"),
        path(f"{base}ticket/<uuid:pk>/reject/", recovery_ticket_reject,
             name="password_reset_ticket_reject"),
        path(f"{base}<uidb64>/<token>/", password_reset_confirm_view,
             name="password_reset_confirm"),
    ]
