"""Membership applications that lapse, and what becomes of them (2026-10-01,
RODO).

    renewable(application)                              -> bool
    renew(application, *, username, community, notice)  -> application
    prune(*, days=None, now=None)                       -> {"pruned", "accounts_deleted", "kept"}

An application is good for ``LIFETIME_DAYS`` (a week): the code has to be
typed within it. One whose week ran out before its applicant got in has
**lapsed**, and until now stayed that way for ever — the form refused its
address as taken and the code answered "This code has expired." So:

* **Applying again renews it** (:func:`renew`, from the membership
  application view): the same row gets a new code and a new week, the
  community and the privacy notice chosen now, and starts over — pending,
  not verified, and without the references asked for the lapsed attempt (a
  pending one would otherwise admit the applicant to whichever community
  they chose this time). The account the lapsed attempt made is reused under
  the username typed now, so the address still has one account and the
  acceptance (``ReferenceRequest.save``, :func:`applicant_account`) finds it.
* **So does one whose every reference was declined** (2026-10-01): the
  decline mail tells the applicant they may apply again, yet the address
  stayed taken for the rest of the week (:func:`declined`).
* **The nightly housekeeping prunes it** (:func:`prune`, from
  ``toto.core.housekeeping``) once it lapsed more than
  ``SOCIALHUB_EXPIRED_APPLICATION_DAYS`` (30) days ago, together with the
  accounts it made.

Both only while nobody with its address got in, and nothing is anybody's:
every account with that e-mail (in any case) was never active, never signed
in, is not staff, and owns nothing but what sign-up gives every account —
``erase_user``'s own walk (Django's deletion collector) is the proof
(:func:`owns_nothing_else`). The application of a member who was admitted is
their record, never housekeeping's — known by its accepted reference
(:func:`admitted`), not only by an account at its address, which a member
who changed their e-mail no longer has; one whose account holds anything at
all — a person, a privacy acceptance, a data export, an erasure request, a
file — is kept, account and all, and counted.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

log = logging.getLogger("toto.socialhub")

#: How long an application is good for, in days.
LIFETIME_DAYS = 7

#: Days a lapsed application is kept before housekeeping prunes it, unless the
#: host sets ``SOCIALHUB_EXPIRED_APPLICATION_DAYS``.
DEFAULT_EXPIRED_DAYS = 30

#: What the platform makes for EVERY account the moment it exists, by the
#: economy's sign-up receivers: toto.assets' prepaid ledger account (it stays,
#: detached, as ``erase_user`` keeps it — the ledger is the money trail) and
#: toto.mana's opening fill, a grant and a faucet payout per pool. Not the
#: applicant's doing, so not "something else" they own. Labels as
#: ``erase_user``'s report names them.
SIGNUP_ROWS = frozenset({
    "assets.LedgerAccount.user",
    "assets.FaucetPayout",
    "mana.ManaGrant",
    "mana.ManaGrant.payout",
})


def expired_days() -> int:
    return max(0, int(getattr(settings, "SOCIALHUB_EXPIRED_APPLICATION_DAYS",
                              DEFAULT_EXPIRED_DAYS)))


def accounts_of(application) -> list:
    """Every account with the application's address, in any case."""
    return list(get_user_model().objects.filter(email__iexact=application.email)
                .order_by("pk"))


def applicant_account(application, *, waiting_only=False):
    """The account ``application`` made: the one its acceptance activates
    (``ReferenceRequest.save``), a renewal names again, and the reference
    step sets the chosen password on.

    The first account with the exact address used to answer (2026-10-01): a
    case variant missed the applicant's account, and an address a member
    also has found the MEMBER's — activated and enrolled in their stead, and
    given the password typed on that public page. So: the accounts at the
    address in any case, one that has not got in preferred, the exact
    spelling first, then the oldest. ``waiting_only`` stops there (None when
    every one got in); else the first of the others answers.
    """
    accounts = accounts_of(application)
    waiting = [account for account in accounts if not got_in(account)]
    pool = waiting if (waiting or waiting_only) else accounts
    exact = [account for account in pool if account.email == application.email]
    return (exact or pool or [None])[0]


def got_in(account) -> bool:
    """Active, signed in once, or staff: somebody's account, not a leftover."""
    return bool(account.is_active or account.last_login or account.is_staff
                or account.is_superuser)


def owns_nothing_else(account) -> bool:
    """Would erasing ``account`` take nothing but it and its sign-up rows?

    ``erase_user``'s ``plan`` walks Django's deletion collector over the
    account: any other row it would delete or detach, or one that blocks the
    delete, and the answer is no. (The report lists every related table,
    most at 0 — only the ones with rows count.)
    """
    from toto.core.management.commands.erase_user import plan

    report = plan(account)
    if report["blocked_by"]:
        return False
    own = type(account)._meta.label
    held = {label for label, n in report["deleted"].items() if n and label != own}
    held |= {label for label, n in report["detached"].items() if n}
    return not held - SIGNUP_ROWS


def admitted(application) -> bool:
    """Did somebody get in through ``application`` — a reference accepted?
    Then it is that member's record (2026-10-01): a member who changed their
    e-mail on My account has no account at its address any more, and without
    this their application read as a leftover — pruned, references and all,
    or renewed by whoever typed the old address."""
    return application.reference_requests.filter(status="accepted").exists()


def leftovers(application):
    """The accounts ``application`` made, when nobody got in through it and
    every account with its address never got in and owns nothing else;
    ``None`` when any did or does. An empty list is an application whose
    account is already gone."""
    if admitted(application):
        return None
    accounts = accounts_of(application)
    if any(got_in(account) for account in accounts):
        return None
    if not all(owns_nothing_else(account) for account in accounts):
        return None
    return accounts


def declined(application) -> bool:
    """Was every reference asked for ``application`` declined — at least one,
    none pending or accepted? Then it is over, as a lapsed one is."""
    statuses = set(application.reference_requests.values_list("status", flat=True))
    return statuses == {"declined"}


def renewable(application) -> bool:
    """Has ``application`` lapsed, or had every reference declined, so that
    applying again renews it?"""
    return ((application.is_expired() or declined(application))
            and leftovers(application) is not None)


def _new_code(old: str) -> str:
    from toto.socialhub.models import MembershipApplication, generate_code

    for _attempt in range(100):
        code = generate_code()
        if code != old and not MembershipApplication.objects.filter(code=code).exists():
            return code
    raise RuntimeError("no free application code")


@transaction.atomic
def renew(application, *, username, community, notice):
    """Start a lapsed application over (see the module docstring).

    The caller has checked :func:`renewable` and that ``username`` is free
    or already this account's (``MembershipApplicationForm``).
    """
    User = get_user_model()
    account = applicant_account(application)
    if account is None:
        User.objects.get_or_create(username=username, defaults={
            "email": application.email, "is_active": False})
    elif account.username != username:
        account.username = username
        account.save(update_fields=["username"])
    application.reference_requests.all().delete()
    now = timezone.now()
    application.community = community
    application.code = _new_code(application.code)
    application.expires_at = now + timedelta(days=LIFETIME_DAYS)
    application.status = "pending"
    application.verified_at = None
    application.privacy_version = notice.version
    application.privacy_accepted_at = now
    # SOCIALHUB.APPLICATION_RENEWED rather than a status change (audit.py).
    application._audit_renewed = True
    application.save()
    return application


def prune(*, days=None, now=None) -> dict:
    """Delete the applications that lapsed more than ``days`` ago (default
    ``SOCIALHUB_EXPIRED_APPLICATION_DAYS``) with the accounts they made.

    Counts only, for the night's one record: ``pruned`` applications,
    ``accounts_deleted`` with them, and ``kept`` — those an account with
    their address still holds something for (or that failed: the reason is
    logged and the next night tries again). A member's application is not
    a candidate at all and is not counted.
    """
    from toto.socialhub.models import MembershipApplication

    days = expired_days() if days is None else max(0, int(days))
    cutoff = (now or timezone.now()) - timedelta(days=days)
    counts = {"pruned": 0, "accounts_deleted": 0, "kept": 0}
    due = list(MembershipApplication.objects.filter(expires_at__lt=cutoff)
               .order_by("pk").values_list("pk", flat=True))
    for pk in due:
        try:
            with transaction.atomic():
                application = MembershipApplication.objects.filter(pk=pk).first()
                if application is None or admitted(application):
                    continue
                accounts = accounts_of(application)
                if any(got_in(account) for account in accounts):
                    continue
                if not all(owns_nothing_else(account) for account in accounts):
                    counts["kept"] += 1
                    continue
                for account in accounts:
                    account.delete()
                application.delete()
        except Exception as exc:  # noqa: BLE001 - one application never stops the night
            counts["kept"] += 1
            log.warning("housekeeping: application %s not pruned (%s)", pk, type(exc).__name__)
            continue
        counts["pruned"] += 1
        counts["accounts_deleted"] += len(accounts)
    return counts
