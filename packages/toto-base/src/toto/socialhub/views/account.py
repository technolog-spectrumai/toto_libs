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
notice through ``toto.core.notices.send_notice``.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.core.client_ip import client_ip
from toto.core.notices import send_notice
from toto.core.user_sessions import end_other_sessions
from toto.people.models import Person
from toto.socialhub import audit
from toto.socialhub.forms import AccountProfileForm, TimeZoneForm, avatar_max_bytes
from toto.ui import PageProcessor

log = logging.getLogger("toto.socialhub")


def own_person(user) -> Person:
    """The member's Person, or an unsaved one the first save creates."""
    person = getattr(user, "community_profile", None)
    if person is None:
        person = Person(user=user, display_name=user.get_username())
    return person


def _page(request, *, profile_form=None, timezone_form=None, password_form=None,
          status=200):
    person = own_person(request.user)
    context = {
        # A federated account signs in at its provider and has no password
        # here to change: the section says so instead of offering a form
        # whose "current password" nothing could ever match.
        "has_password": request.user.has_usable_password(),
        "password_form": password_form or PasswordChangeForm(request.user),
        "person": person,
        "profile_form": profile_form or AccountProfileForm(instance=person),
        "timezone_form": timezone_form or TimeZoneForm(
            initial={"timezone": person.timezone}),
        "platform_time_zone": settings.TIME_ZONE,
        "avatar_max_mb": avatar_max_bytes() // (1024 * 1024),
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
    form = PasswordChangeForm(user, request.POST)
    if not form.is_valid():
        return _page(request, password_form=form, status=400)
    form.save()
    update_session_auth_hash(request, user)
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
