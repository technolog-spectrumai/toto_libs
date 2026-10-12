"""The doors of the invitation's way in (``toto.socialhub.invitations``).

Three, all 404 on a platform that takes applications instead
(``enrollment.invitation_only``):

* ``invitations`` — the socialhub's Invitations tab, staff only: issue one
  (the link is shown once, and mailed when an address was named) and the
  list of those issued, each with its state.
* ``invitation_revoke`` — POST, staff only.
* ``invitation_accept`` — the one page a visitor reaches. An unknown, spent,
  revoked or expired token answers the same 404 page, so nothing is learned
  by guessing; misses are rate-limited by address and an open link's form
  by link (so the people of one office, behind one address, are not locked
  out by one another). The privacy notice is accepted
  here as on the application's form: the page links the current version,
  the form carries its number and refuses without the tick, and no account
  is made while no notice is published.
"""
from __future__ import annotations

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, login, password_validation
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from toto.core import ratelimit
from toto.core.client_ip import client_ip
from toto.ui import PageProcessor

from .. import audit, invitations
from ..enrollment import invitation_only
from ..models import Community, Invitation, PrivacyNotice

#: Tries of the accept page one address gets in an hour.
ACCEPTS_PER_ADDRESS = 20
ACCEPT_WINDOW = 3600
#: Invitations the list shows, newest first.
LISTED = 200


class InvitationForm(forms.Form):
    community = forms.ModelChoiceField(
        label=gettext_lazy("Community"), queryset=Community.objects.none(),
        help_text=gettext_lazy("The community the new member joins."))
    email = forms.EmailField(
        label=gettext_lazy("E-mail address"), max_length=254, required=False,
        help_text=gettext_lazy("When given, the account made with this link gets this address "
                               "and the link is mailed there."))
    note = forms.CharField(
        label=gettext_lazy("Note"), max_length=200, required=False,
        help_text=gettext_lazy("Who this is for, for the list; the invitation's page does not "
                               "show it."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        communities = Community.objects.order_by("name")
        self.fields["community"].queryset = communities
        if not self.is_bound and communities.count() == 1:
            self.fields["community"].initial = communities.first().pk

    def clean_email(self):
        address = (self.cleaned_data.get("email") or "").strip()
        if address and get_user_model().objects.filter(email__iexact=address).exists():
            raise forms.ValidationError(_("An account already has this address."))
        return address


class InvitationAcceptForm(forms.Form):
    username = forms.CharField(label=gettext_lazy("Username"), max_length=150,
                               widget=forms.TextInput(attrs={"autocomplete": "username"}))
    display_name = forms.CharField(label=gettext_lazy("Display name"), max_length=150)
    email = forms.EmailField(label=gettext_lazy("E-mail address"), max_length=254, required=False)
    password1 = forms.CharField(label=gettext_lazy("Password"), strip=False,
                                widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label=gettext_lazy("Password, again"), strip=False,
                                widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    # The privacy notice (RODO), as on the application's form: the version
    # shown rides along, so an acceptance is of the text the person read. A
    # version published while the page was open is refused, and the page then
    # shows the new one.
    privacy_version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    privacy_accept = forms.BooleanField(
        required=True, label=gettext_lazy("I have read the privacy notice and accept it."),
        error_messages={"required": gettext_lazy("Please read the privacy notice and tick the "
                                                 "box to make your account.")})

    def __init__(self, *args, invitation, **kwargs):
        self.invitation = invitation
        super().__init__(*args, **kwargs)
        self.privacy_notice = PrivacyNotice.current()
        if self.privacy_notice is not None:
            self.fields["privacy_version"].initial = self.privacy_notice.version
        if invitation.email:
            # The address the link was issued to is the account's; it is not
            # a choice here.
            self.fields["email"].initial = invitation.email
            self.fields["email"].disabled = True

    def clean_username(self):
        User = get_user_model()
        # As the account will be stored (Django normalises to NFKC), so a
        # compatibility spelling of a taken name is refused here, not at the
        # insert.
        username = User.normalize_username(self.cleaned_data["username"].strip())
        for validator in User._meta.get_field("username").validators:
            validator(username)
        # Nor another account's e-mail address, the application form's rule:
        # its owner typing their address where the username goes would be
        # trying THIS account.
        own = (self.invitation.email or self.data.get(self.add_prefix("email")) or "").strip()
        taken = User.objects.filter(username__iexact=username)
        if username.casefold() != own.casefold():
            taken = taken | User.objects.filter(email__iexact=username)
        if taken.exists():
            raise forms.ValidationError(_("This username is already taken."))
        return username

    def clean_email(self):
        # No second account at an address somebody holds (an account's, a
        # person's), checked for the invitation's own address too: two
        # invitations may have named it, or its owner arrived another way
        # since. And a typed address must not be one another invitation is
        # made out to: nothing here proves the person typing it reads its
        # mail.
        fixed = self.invitation.email
        address = fixed or (self.cleaned_data.get("email") or "").strip()
        if address and (invitations.address_held(address)
                        or (not fixed and invitations.made_out_to(address,
                                                                 but=self.invitation))):
            raise forms.ValidationError(_("This address cannot be used here. Ask for a new "
                                          "invitation."))
        return address

    def clean(self):
        cleaned = super().clean()
        notice = self.privacy_notice
        if notice is None:
            raise forms.ValidationError(_("Accounts cannot be made with an invitation until a "
                                          "privacy notice is published."))
        if cleaned.get("privacy_accept") and cleaned.get("privacy_version") != notice.version:
            self.add_error("privacy_accept", _(
                "The privacy notice has changed since this page was opened. Please read the "
                "current version and tick the box again."))
            self.data = self.data.copy()
            self.data[self.add_prefix("privacy_version")] = str(notice.version)
            self.data.pop(self.add_prefix("privacy_accept"), None)
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", _("The two passwords differ."))
        elif p1:
            probe = get_user_model()(username=cleaned.get("username", ""),
                                     email=cleaned.get("email", ""))
            try:
                password_validation.validate_password(p1, probe)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned


def _render(request, template, context, status=200):
    return render(request, template, PageProcessor().decorate(context, request), status=status)


def _staff_only(request):
    if not invitations.may_issue(request.user):
        raise PermissionDenied(_("Invitations are issued by the platform's team."))


@invitation_only
@login_required
def invitation_list(request):
    _staff_only(request)
    form = InvitationForm(request.POST or None)
    link = ""
    mailed = None
    if request.method == "POST" and form.is_valid():
        row, token = invitations.issue(
            issued_by=request.user, community=form.cleaned_data["community"],
            email=form.cleaned_data["email"], note=form.cleaned_data["note"])
        audit.invitation_issued(row, request=request)
        link = request.build_absolute_uri(reverse("socialhub:invitation_accept", args=[token]))
        mailed = invitations.mail(row, link) if row.email else None
        form = InvitationForm()
        messages.success(request, _("The invitation is issued. The link below is shown once."))
    rows = Invitation.objects.select_related("community", "issued_by", "accepted_user")[:LISTED]
    response = _render(request, "socialhub/invitations.html", {
        "form": form, "rows": rows, "link": link, "mailed": mailed,
        "hours": invitations.hours(), "active_tab": "invitations",
    })
    if link:
        # The one answer that carries the link: never stored.
        response["Cache-Control"] = "no-store"
    return response


@invitation_only
@login_required
@require_POST
def invitation_revoke(request, pk):
    _staff_only(request)
    row = get_object_or_404(Invitation, pk=pk)
    if row.accepted_at is None and row.revoked_at is None:
        row.revoked_at = timezone.now()
        row.save(update_fields=["revoked_at"])
        audit.invitation_revoked(row, request=request)
        messages.success(request, _("The invitation is revoked."))
    return redirect("socialhub:invitations")


def _issuer_stands(invitation) -> bool:
    """A link works while the account that issued it is still active staff;
    one whose issuer is gone altogether (erased) stays the platform's."""
    return invitation.issued_by_id is None or invitations.may_issue(invitation.issued_by)


@invitation_only
def invitation_accept(request, token):
    if request.user.is_authenticated:
        messages.info(request, _("You are signed in already; an invitation makes a new account."))
        return redirect("core:dashboard")
    invitation = Invitation.open_for(token)
    if invitation is not None and not _issuer_stands(invitation):
        invitation = None
    if invitation is None:
        # Misses are counted by address: guessing, or a link gone stale.
        hit = ratelimit.hit(f"socialhub:invite:{client_ip(request)}", limit=ACCEPTS_PER_ADDRESS,
                            window=ACCEPT_WINDOW)
        if not hit.allowed:
            return _render(request, "socialhub/invitation_accept.html",
                           {"invitation": None, "throttled": True}, status=429)
        return _render(request, "socialhub/invitation_accept.html", {"invitation": None},
                       status=404)
    # An open link's form is counted by link, so its holder cannot ask it
    # without end which usernames and addresses are taken.
    if request.method == "POST" and not ratelimit.hit(
            f"socialhub:invite:form:{invitation.pk}", limit=ACCEPTS_PER_ADDRESS,
            window=ACCEPT_WINDOW).allowed:
        return _render(request, "socialhub/invitation_accept.html",
                       {"invitation": None, "throttled": True}, status=429)
    form = InvitationAcceptForm(request.POST or None, invitation=invitation)
    if request.method == "POST" and form.is_valid():
        version = form.privacy_notice.version
        with transaction.atomic():
            row = Invitation.objects.select_for_update().get(pk=invitation.pk)
            if not row.is_open or not _issuer_stands(row):
                # Closed between the page and the form: the page every
                # closed link gets.
                return _render(request, "socialhub/invitation_accept.html",
                               {"invitation": None}, status=404)
            user, _person = invitations.accept(
                row, username=form.cleaned_data["username"],
                display_name=form.cleaned_data["display_name"],
                email=form.cleaned_data["email"], password=form.cleaned_data["password1"],
                privacy_version=version)
        audit.invitation_accepted(row, user, version, request=request)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, _("Welcome, your account is made."))
        return redirect("core:dashboard")
    return _render(request, "socialhub/invitation_accept.html",
                   {"invitation": invitation, "form": form,
                    "privacy_notice": form.privacy_notice},
                   status=400 if request.method == "POST" else 200)
