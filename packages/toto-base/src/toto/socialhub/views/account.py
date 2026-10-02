"""My account (2026-09-30): the one page where a member changes their own
account — reached from the top bar, mounted by the host at ``/account/``.

It lives in the socialhub because what it edits first is the member's Person
(profile, avatar, time zone), which the socialhub already shows and edits
(`views/profile.py`); ``toto.core`` owns the sign-in helpers the later sections
call, not a page. Its own URL module (`account_urls.py`, namespace
``account``) so the address is short and stays put if the socialhub's own
prefix ever moves.

Own account only, by construction: no view here takes a person, a slug or a
user id — whatever a form names, the row written is ``request.user``'s. This
page never creates or deletes an ACCOUNT (console only); it creates the Person
row on a first save, the way `set_my_address` does.

Every successful change is a ``SOCIALHUB.PROFILE_CHANGED`` record naming the
fields, never their values; a password change is ``AUTH.PASSWORD_CHANGED``
(the auth trail's, where the member's sign-ins are) and mails the member a
notice through ``toto.core.notices.send_notice``. Ending a session, one or
all but this one, is ``AUTH.SESSION_ENDED`` / ``AUTH.SIGNED_OUT_EVERYWHERE``
(``toto.core.user_sessions`` keeps the rows the list is made of).

Changing the e-mail address is two steps (``toto.socialhub.email_change``):
the form mails a single-use link to the new address, and the change happens
only when that link is opened by the same member, signed in, within a day —
``AUTH.EMAIL_CHANGE_REQUESTED`` then ``AUTH.EMAIL_CHANGED``, addresses masked;
the old address is told.

Recent sign-ins lists the member's own ``AUTH.*`` records of the last 30
days, read through ``toto.audit.queries.member_auth_records`` — the audit
pages stay staff-only; this is the one narrow read of rows about the member,
guesses at their name included.

Key store (2026-10-01): the member creates their own personal key store —
a gervazy strongbox with its first keys, under a passphrase they choose —
through ``toto.gervazy.personal``, which refuses when it already exists and
never overwrites. ``AUTH.KEY_STORE_CREATED`` names the box only.

Your data (2026-10-01, RODO): *Download my data* queues a copy of everything
the platform holds about the member into their own personal bucket
(``toto.socialhub.data_export``, the console's ``export_user`` builder), one
at a time and once a day, and the section shows how the last one went.

Erase my account (2026-10-01, RODO): the member FILES a request
(``toto.socialhub.erasure``), after a confirmation saying an operator carries
it out at the console and what is kept. Nothing here erases anything — the
account goes only by ``erase_user`` at the console, which closes the request.
"""

from __future__ import annotations

import logging

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from toto.core.client_ip import client_ip
from toto.core.notices import send_notice
from toto.core.user_sessions import end_other_sessions, end_session, rekey, sessions_for
from toto.people.models import Person
from toto.socialhub import audit, email_change
from toto.socialhub.forms import (
    AccountEmailForm,
    AccountProfileForm,
    KeyStoreForm,
    TimeZoneForm,
    avatar_max_bytes,
)
from toto.ui import PageProcessor

log = logging.getLogger("toto.socialhub")


def own_person(user) -> Person:
    """The member's Person, or an unsaved one the first save creates."""
    person = getattr(user, "community_profile", None)
    if person is None:
        person = Person(user=user, display_name=user.get_username())
    return person


#: Sessions shown per page of the Sessions section.
SESSIONS_PER_PAGE = 20


def _sessions_page(request):
    """The member's live sessions, the one in use first and marked."""
    current = request.session.session_key
    rows = sessions_for(request.user)
    for row in rows:
        row.is_current = bool(current) and row.session_key == current
    rows.sort(key=lambda row: not row.is_current)  # stable: then most recent first
    return Paginator(rows, SESSIONS_PER_PAGE).get_page(request.GET.get("page"))


#: Sign-in records shown per page, how far back, and the page's own query
#: parameter — the Sessions list already has ``?page=``.
SIGNINS_PER_PAGE = 20
SIGNINS_DAYS = 30
SIGNINS_PAGE_PARAM = "signins_page"

#: What each ``AUTH.*`` action is called on the list; one not named here
#: shows as its action code rather than being hidden.
SIGNIN_LABELS = {
    "AUTH.LOGIN": gettext_lazy("Signed in"),
    "AUTH.LOGOUT": gettext_lazy("Signed out"),
    "AUTH.LOGIN_FAILED": gettext_lazy("Failed sign-in"),
    "AUTH.LOCKED": gettext_lazy("Sign-in paused after failed attempts"),
    "AUTH.UNLOCKED": gettext_lazy("Sign-in pause lifted"),
    "AUTH.TOKEN_REFUSED": gettext_lazy("Desktop sign-in refused"),
    "AUTH.PASSWORD_CHANGED": gettext_lazy("Password changed"),
    "AUTH.PASSWORD_RESET": gettext_lazy("Password reset through a link"),
    "AUTH.EMAIL_CHANGE_REQUESTED": gettext_lazy("New e-mail address asked for"),
    "AUTH.EMAIL_CHANGED": gettext_lazy("E-mail address changed"),
    "AUTH.KEY_STORE_CREATED": gettext_lazy("Key store created"),
    "AUTH.SESSION_ENDED": gettext_lazy("Session ended"),
    "AUTH.SIGNED_OUT_EVERYWHERE": gettext_lazy("Signed out everywhere else"),
    "AUTH.ACCOUNT_CREATED": gettext_lazy("Account created"),
    "AUTH.ACCOUNT_ACTIVATED": gettext_lazy("Account activated"),
    "AUTH.ACCOUNT_DEACTIVATED": gettext_lazy("Account deactivated"),
    "AUTH.STAFF_GRANTED": gettext_lazy("Staff access granted"),
    "AUTH.STAFF_REVOKED": gettext_lazy("Staff access removed"),
    "AUTH.SUPERUSER_GRANTED": gettext_lazy("Superuser access granted"),
    "AUTH.SUPERUSER_REVOKED": gettext_lazy("Superuser access removed"),
}

#: A refused sign-in's ``refused`` reason, said as a note beside it.
REFUSAL_NOTES = {
    "delay": gettext_lazy("refused: too many attempts, wait a moment"),
    "locked": gettext_lazy("refused: sign-in paused"),
    "address_locked": gettext_lazy("refused: this address is paused"),
}


def _signin_row(record, user):
    """One record as the list shows it: never the record's own metadata.

    The address and browser are those of whoever's request it was. When that
    was ANOTHER account — a staff member changing this account's flags — they
    are that person's, not the member's to see, and are left out.
    """
    by_other = bool(record.actor_user_id) and record.actor_user_id != user.pk
    source = record.request_source or {}
    metadata = record.metadata or {}
    address = "" if by_other else (source.get("ip_address") or "")
    if not address and not by_other and record.action == "AUTH.LOCKED":
        address = metadata.get("address") or ""
    return {
        "timestamp": record.timestamp,
        "action": record.action,
        "label": SIGNIN_LABELS.get(record.action, record.action),
        "note": REFUSAL_NOTES.get(str(metadata.get("refused", "")), ""),
        "success": record.success,
        "by_other": by_other,
        "address": address,
        "user_agent": "" if by_other else (source.get("user_agent") or ""),
    }


def _signins_page(request):
    from django.apps import apps

    if not apps.is_installed("toto.audit"):
        return None
    from toto.audit.queries import member_auth_records

    records = member_auth_records(request.user, days=SIGNINS_DAYS)
    page = Paginator(records, SIGNINS_PER_PAGE).get_page(
        request.GET.get(SIGNINS_PAGE_PARAM))
    page.rows = [_signin_row(record, request.user) for record in page.object_list]
    return page


def _key_store(request, form=None):
    """The Key store section's context, or None where gervazy is not installed."""
    if not apps.is_installed("toto.gervazy"):
        return None
    from toto.gervazy.personal import PASSPHRASE_MIN_LENGTH, is_keyed, personal_strongbox

    box = personal_strongbox(request.user)
    return {
        "box": box,
        "keyed": bool(box) and is_keyed(box),
        "form": form or KeyStoreForm(),
        "min_length": PASSPHRASE_MIN_LENGTH,
    }


def _data_export(request):
    """The Your data section's context: the latest export and when the next
    may be asked for (``toto.socialhub.data_export``)."""
    from toto.socialhub import data_export

    data_export.close_stale(request.user)
    latest = data_export.latest_for(request.user)
    download_url = ""
    if latest is not None and latest.status == latest.READY and latest.output_id:
        download_url = latest.output.get_public_url() or ""
    return {"latest": latest, "download_url": download_url,
            "next_allowed_at": data_export.next_allowed_at(request.user),
            "interval_hours": int(data_export.INTERVAL.total_seconds() // 3600)}


def _erasure(request):
    """The Erase my account section's context: the member's own latest
    request, and whether they may see the operators' list."""
    from toto.socialhub import erasure
    from toto.socialhub.views.privacy import may_publish

    return {"latest": erasure.latest_for(request.user),
            "may_handle": may_publish(request.user)}


def _page(request, *, profile_form=None, timezone_form=None, password_form=None,
          email_form=None, key_store_form=None, status=200):
    person = own_person(request.user)
    sessions = _sessions_page(request)
    signins = _signins_page(request)
    # Each list pages on its own parameter and carries the other's along, so
    # paging one does not send the other back to its first page.
    sessions_query = ""
    if signins is not None and signins.number > 1:
        sessions_query = f"&{SIGNINS_PAGE_PARAM}={signins.number}"
    signins_query = f"&page={sessions.number}" if sessions.number > 1 else ""
    context = {
        "page_obj": sessions,
        "is_paginated": sessions.has_other_pages(),
        "sessions": sessions.object_list,
        "sessions_query": sessions_query + "#sessions",
        "signins_page": signins,
        "signins_paginated": bool(signins) and signins.has_other_pages(),
        "signins_query": signins_query + "#signins",
        "signins_page_param": SIGNINS_PAGE_PARAM,
        "signins_days": SIGNINS_DAYS,
        # A federated account signs in at its provider and has no password
        # here to change: the section says so instead of offering a form
        # whose "current password" nothing could ever match.
        "has_password": request.user.has_usable_password(),
        "password_form": password_form or PasswordChangeForm(request.user),
        # The e-mail section hides behind has_password too: a federated
        # account's address is its provider's, rewritten at each sign-in.
        "email_form": email_form or AccountEmailForm(user=request.user),
        "email_pending": email_change.pending_for(request.user),
        "email_link_hours": email_change.LINK_HOURS,
        "person": person,
        "profile_form": profile_form or AccountProfileForm(instance=person),
        "timezone_form": timezone_form or TimeZoneForm(
            initial={"timezone": person.timezone}),
        "platform_time_zone": settings.TIME_ZONE,
        "avatar_max_mb": avatar_max_bytes() // (1024 * 1024),
        "key_store": _key_store(request, key_store_form),
        "data_export": _data_export(request),
        "erasure": _erasure(request),
    }
    context = PageProcessor().decorate(context, request)
    return render(request, "socialhub/account.html", context, status=status)


@login_required
def account(request):
    return _page(request)


@require_POST
@login_required
def account_profile(request):
    person = own_person(request.user)
    old_avatar = person.avatar.name if person.pk and person.avatar else ""
    form = AccountProfileForm(request.POST, request.FILES, instance=person)
    if not form.is_valid():
        return _page(request, profile_form=form, status=400)
    person = form.save()
    changed = list(form.changed_data)
    if "avatar" in changed and old_avatar and old_avatar != person.avatar.name:
        # The picture the member replaced or removed goes with it: a removed
        # photo left in /media/ is still a photo of them on the server.
        try:
            person.avatar.storage.delete(old_avatar)
        except Exception:  # noqa: BLE001 - the new profile is saved either way
            log.warning("account: could not delete old avatar %s", old_avatar)
    audit.profile_changed(person, changed)
    messages.success(request, _("Your profile is saved."))
    return redirect("account:home")


@require_POST
@login_required
def account_timezone(request):
    form = TimeZoneForm(request.POST)
    if not form.is_valid():
        return _page(request, timezone_form=form, status=400)
    person = own_person(request.user)
    zone = form.cleaned_data["timezone"]
    if person.timezone != zone:
        # The choice list is zoneinfo's own, so the form has validated it.
        person.timezone = zone
        if person.pk:
            person.save(update_fields=["timezone"])
        else:
            person.save()
        audit.profile_changed(person, ["timezone"])
    if zone:
        messages.success(request, _("Times are now shown in %(zone)s.") % {"zone": zone})
    else:
        messages.success(request, _("Times are now shown in the platform's time zone."))
    return redirect("account:home")


def _password_changed_on_chain(user, request, ended):
    from django.apps import apps

    if not apps.is_installed("toto.audit"):
        return None
    from toto.audit.identity import on_password_changed

    return on_password_changed(user, request=request, sessions_ended=ended)


def _password_guess_refused(request, user):
    """The sign-in lockout's refusal for this member from this address, or
    None — and then the try is counted, before the password is compared
    (``begin_try``, 2026-10-02: counted after, tries sent at the same moment
    were all compared): a wrong password keeps the count
    (``_password_guess_failed``), a right one gives it back
    (``_password_not_a_guess``)."""
    from toto.core import signin_lockout

    if not signin_lockout.enabled():
        return None
    return signin_lockout.begin_try(request, user.get_username())


def _password_guess_failed(request, user):
    """A wrong current password counts as a failed sign-in for this member here."""
    from toto.core import signin_lockout

    if signin_lockout.enabled():
        signin_lockout.note_failure(request, user.get_username())


def _password_not_a_guess(request):
    """The current password was right: the try counted for it is given back."""
    from toto.core import signin_lockout

    signin_lockout.release_try(request)


@sensitive_post_parameters("old_password", "new_password1", "new_password2")
@require_POST
@login_required
def account_password(request):
    """Change one's own password, signed in (2026-09-30).

    Django's ``PasswordChangeForm``: the current password, then the new one
    twice through ``AUTH_PASSWORD_VALIDATORS``. On success this session is
    re-signed with the new hash (``update_session_auth_hash``, which also
    gives it a new key) and every OTHER session of the member is ended —
    browsers and desktop tokens alike: whoever else held a sign-in made with
    the old password loses it now, not at their next request.
    """
    user = request.user
    if not user.has_usable_password():
        messages.error(request, _("Your account signs in through another service; "
                                  "change your password there."))
        return redirect("account:home")
    # The current password is a password guess like any at the sign-in form
    # (2026-10-01): a borrowed or stolen session must not get unlimited tries
    # at it. PasswordChangeForm checks it with check_password, which no
    # lockout sees, so the lockout is asked first and told of each miss here.
    held = _password_guess_refused(request, user)
    if held is not None:
        messages.error(request, held.message)
        return redirect(f"{reverse('account:home')}#password")
    form = PasswordChangeForm(user, request.POST)
    if not form.is_valid():
        if form.has_error("old_password"):
            _password_guess_failed(request, user)
        else:
            _password_not_a_guess(request)
        return _page(request, password_form=form, status=400)
    _password_not_a_guess(request)
    form.save()
    old_key = request.session.session_key
    update_session_auth_hash(request, user)
    # A new key for this session; its row on the Sessions list follows it.
    rekey(user, old_key, request.session.session_key)
    ended = end_other_sessions(user, keep=request.session.session_key)
    _password_changed_on_chain(user, request, ended)
    send_notice(user, "password_changed",
                {"address": client_ip(request), "sessions_ended": ended})
    if ended:
        messages.success(request, _("Your password is changed. Your other sign-ins "
                                    "were ended; sign in there again with the new one."))
    else:
        messages.success(request, _("Your password is changed."))
    return redirect("account:home")


def _on_chain(name, user, request, **values):
    from django.apps import apps

    if not apps.is_installed("toto.audit"):
        return None
    from toto.audit import identity

    return getattr(identity, name)(user, request=request, **values)


@require_POST
@login_required
def account_session_end(request, session_id):
    """End one of the member's own sessions (2026-09-30).

    ``session_id`` is the list's row id, never a key. Another member's id and
    one that is already gone are the same 404. The session in use is not
    ended here: that is signing out, which the top bar does.
    """
    current = request.session.session_key
    if current and request.user.signed_in_sessions.filter(
            pk=session_id, session_key=current).exists():
        messages.info(request, _("That is the session you are using now; "
                                 "sign out to end it."))
        return redirect("account:home")
    row = end_session(request.user, session_id, request=request)
    if row is None:
        raise Http404
    _on_chain("on_session_ended", request.user, request, kind=row.kind, session_id=row.pk)
    messages.success(request, _("That session is ended; it has to sign in again."))
    return redirect("account:home")


@require_POST
@login_required
def account_sessions_end_others(request):
    """Sign out everywhere else: every session but this one (2026-09-30)."""
    ended = end_other_sessions(request.user, keep=request.session.session_key)
    _on_chain("on_signed_out_everywhere", request.user, request, sessions_ended=ended)
    if ended:
        messages.success(request, _("Every other session is ended; "
                                    "you are signed in here only."))
    else:
        messages.info(request, _("There was no other session to end."))
    return redirect("account:home")


@sensitive_post_parameters("password")
@require_POST
@login_required
def account_email(request):
    """Ask to move one's account to a new address (2026-09-30).

    Changes nothing on the account: it mails the confirmation link to the new
    address and says so. See ``toto.socialhub.email_change``.
    """
    if not request.user.has_usable_password():
        messages.error(request, _("Your account signs in through another service; "
                                  "change your e-mail address there."))
        return redirect("account:home")
    if email_change.throttled(request.user):
        messages.error(request, _("You have asked for a new address too often. "
                                  "Try again in an hour."))
        return redirect(f"{reverse('account:home')}#email")
    held = _password_guess_refused(request, request.user)
    if held is not None:
        messages.error(request, held.message)
        return redirect(f"{reverse('account:home')}#email")
    form = AccountEmailForm(request.POST, user=request.user)
    if not form.is_valid():
        if form.has_error("password"):
            _password_guess_failed(request, request.user)
        else:
            _password_not_a_guess(request)
        return _page(request, email_form=form, status=400)
    _password_not_a_guess(request)
    address = form.cleaned_data["new_email"]
    if email_change.throttled(request.user, address):
        messages.error(request, _("That address has been sent enough links for today. "
                                  "Try again tomorrow."))
        return redirect(f"{reverse('account:home')}#email")
    if email_change.request_change(request.user, address, request=request):
        messages.success(request, _("A link is on its way to %(address)s. Open it while "
                                    "signed in here to use that address; until then "
                                    "nothing changes.") % {"address": address})
    else:
        messages.error(request, _("The confirmation mail could not be sent. Try again "
                                  "later, or tell an administrator."))
    return redirect("account:home")


#: What the member is told when a link is refused, by outcome.
EMAIL_REFUSALS = {
    email_change.INVALID: gettext_lazy(
        "That link is not valid any more: it was used already, or a newer one "
        "replaced it. Your address is unchanged."),
    email_change.EXPIRED: gettext_lazy(
        "That link has expired. Your address is unchanged; ask for a new one below."),
    email_change.NOT_YOURS: gettext_lazy(
        "That link was sent for another account. Sign in as that account to "
        "open it; nothing was changed."),
    email_change.TAKEN: gettext_lazy(
        "That address has been taken by another account meanwhile. Your "
        "address is unchanged."),
}


@login_required
def account_email_confirm(request):
    """The mailed link (2026-09-30): ``?token=`` — a query parameter, so the
    path the chain keeps never carries it.

    A GET that changes the account, on purpose: the link is opened from a mail
    program, the token binds it to one member and one address, and it acts
    only for that member's own signed-in session. Signed out, the login
    redirect brings the member back here. Post/Redirect/Get either way.
    """
    outcome = email_change.confirm_change(request.user, request.GET.get("token", ""),
                                          request=request)
    if outcome == email_change.CHANGED:
        messages.success(request, _("Your e-mail address is now %(address)s. Your other "
                                    "sign-ins were ended.")
                         % {"address": request.user.email})
    else:
        messages.error(request, EMAIL_REFUSALS[outcome])
    return redirect(f"{reverse('account:home')}#email")


@sensitive_post_parameters("passphrase", "passphrase2")
@require_POST
@login_required
def account_key_store(request):
    """Create one's own key store (2026-10-01); see ``toto.gervazy.personal``.

    Own account only: the box is made for ``request.user`` and nobody else,
    whatever the form carries. One that exists — keyed or bare — is never
    touched; a bare one is initialised on the keys page, which keeps its salt.
    The passphrase goes to the key derivation and nowhere else: not the
    session, not a message, not either audit trail.
    """
    if not apps.is_installed("toto.gervazy"):
        raise Http404
    from toto.gervazy.personal import (
        KeyStoreExists,
        KeyStoreRefused,
        create_personal_strongbox,
    )

    form = KeyStoreForm(request.POST)
    if not form.is_valid():
        return _page(request, key_store_form=form, status=400)
    try:
        box = create_personal_strongbox(request.user, form.cleaned_data["passphrase"],
                                        request=request)
    except KeyStoreExists:
        messages.error(request, _("You already have a key store; nothing was changed."))
        return redirect(f"{reverse('account:home')}#keystore")
    except KeyStoreRefused:
        messages.error(request, _("Your account already has a store that your sealed "
                                  "files depend on, so a new one cannot be made here. "
                                  "Ask an administrator."))
        return redirect(f"{reverse('account:home')}#keystore")
    except Exception:  # noqa: BLE001 - never echo what the crypto layer said
        log.exception("account: key store creation failed for user %s", request.user.pk)
        messages.error(request, _("The key store could not be created. Nothing was saved; "
                                  "try again later."))
        return redirect(f"{reverse('account:home')}#keystore")
    _on_chain("on_key_store_created", request.user, request, strongbox_id=box.pk)
    messages.success(request, _("Your key store is ready. Keep the passphrase somewhere "
                                "safe: it is stored nowhere and cannot be recovered."))
    return redirect(f"{reverse('account:home')}#keystore")


@require_POST
@login_required
def account_data_export(request):
    """Ask for a copy of one's own data (2026-10-01, RODO); see
    ``toto.socialhub.data_export``. Own account only: the export is made for
    ``request.user`` whatever the form carries, and lands in their own
    personal bucket."""
    from toto.socialhub import data_export

    try:
        data_export.request_export(request.user, request=request)
    except data_export.Refused as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Your data is being prepared. The copy will be in your "
                                    "personal bucket, and linked here, when it is ready."))
    return redirect(f"{reverse('account:home')}#data")


@require_POST
@login_required
def account_erasure_request(request):
    """File a request to erase one's own account (2026-10-01, RODO); see
    ``toto.socialhub.erasure``. Files a ticket for ``request.user`` only and
    erases nothing: the erase is the console's."""
    from toto.socialhub import erasure

    if request.POST.get("confirm") != "yes":
        messages.error(request, _("Confirm that you have read what the request does."))
        return redirect(f"{reverse('account:home')}#erasure")
    try:
        erasure.file_request(request.user, request=request)
    except erasure.Refused as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Your request is filed. An operator carries it out at the "
                                    "server's console; until then your account works as "
                                    "before."))
    return redirect(f"{reverse('account:home')}#erasure")
