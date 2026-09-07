"""Patron-authorized password recovery — the flow for a platform without email.

``password_reset.py`` owns the pages; this module owns the rules: who approves
a given user's recovery, when tickets are filed, and when they die. The design
mirrors the membership pipeline's reference request — a card on somebody's
profile that they accept or reject — because that is the shape this platform
already trusts for one person vouching for another.

## Who approves

Resolution order, first match wins; the ticket records which rule fired:

1. **patron** — ``people.Person.patron`` with a live login. The person this
   user's account answers to, when somebody set it.
2. **referrer** — the person whose accepted ``ReferenceRequest`` activated this
   account at signup. The patron field was historically never written by the
   membership pipeline, so for most existing accounts the referrer IS the
   patron in everything but the column — and accepting a reference now sets
   ``patron`` going forward (``socialhub.models``), closing that gap.
3. **staff** — nobody personal could be resolved; the ticket has no approver
   row and appears on every staff member's own profile as a queue.

Every lookup is soft: sso_core ships in toto-auth and must not hard-require
people/socialhub tables at import, and a user with no Person row still gets
the staff rule rather than an error.

## Anti-enumeration

``file_request`` returns None for every failure — unknown username, inactive
account, federated account with no usable password, a pending ticket already
open — and the caller renders the same generic page regardless. The audit
chain records what actually happened; the anonymous requester learns nothing.
"""
from __future__ import annotations

import uuid

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import RecoveryTicket, hash_token, link_ttl, request_ttl


def _audit(action, system=False, **kwargs):
    """Into the audit chain, on hosts that keep one. A no-op elsewhere —
    toto.audit ships in toto-base, but installing it is the host's call.

    ``system=True`` marks an event with NO actor — a sweep that merely ran
    during somebody's page render. Resolved inside the guard, because even
    importing the sentinel would pull audit's models in on a host that does
    not install the app.
    """
    if not django_apps.is_installed("toto.audit"):
        return
    from toto.audit import record, system_actor

    if system:
        kwargs["actor_user"] = system_actor()
    record(action, app_label="sso_core", **kwargs)


#: Rank, and the whole of the escalation rule. An approver must be at least
#: the subject's rank, and anybody above ordinary rank needs a real badge.
#:
#: WHY THIS EXISTS. Until 2026-09-07 ``may_respond`` admitted ANY staff member
#: to a queue ticket, and ``file_request`` refused nobody, so a superuser could
#: be recovered by a member of staff: file a ticket for the manager's username
#: on the public reset page, approve it from your own staff queue, and
#: ``approve`` hands you the one-time link. Three clicks from staff to
#: superuser, and the audit chain recorded it as a routine recovery.
#:
#: Ranks are read off Django's own flags plus one host-nameable list, so a host
#: that grades its people some other way says so in settings rather than
#: patching this module.
RANK_MEMBER = 0
RANK_PROTECTED = 1
RANK_STAFF = 2
RANK_SUPERUSER = 3


def rank(user) -> int:
    """How far up ``user`` stands. Never raises; anonymous is the floor."""
    if user is None or not getattr(user, "is_authenticated", False):
        return RANK_MEMBER
    if user.is_superuser:
        return RANK_SUPERUSER
    if user.is_staff:
        return RANK_STAFF
    from django.conf import settings

    protected = getattr(settings, "RECOVERY_PROTECTED_GROUPS", ()) or ()
    if protected and user.groups.filter(name__in=protected).exists():
        return RANK_PROTECTED
    return RANK_MEMBER


def may_approve_rank(approver_rank: int, subject_rank: int) -> bool:
    """The rule, in one place so all three callers cannot drift apart.

    An approver never recovers somebody who outranks them, and recovering
    anybody above ordinary rank takes a badge — a patron who is an ordinary
    member may vouch for an ordinary member, and for nobody else.
    """
    if approver_rank < subject_rank:
        return False
    if subject_rank > RANK_MEMBER and approver_rank < RANK_STAFF:
        return False
    return True


def resolve_approver(user):
    """(approver_user_or_None, rule) for ``user``. Never raises.

    A personal approver who may not approve this subject is SKIPPED rather
    than returned — the ticket falls through to the staff queue, where the
    rank rule is applied again. That is why a manager's own patron does not
    silently become their recovery route.
    """
    if django_apps.is_installed("toto.people"):
        from toto.people.models import Person

        person = (
            Person.objects.filter(user=user)
            .select_related("patron__user").first()
        )
        if person is not None and person.patron is not None:
            patron_user = person.patron.user
            if (patron_user is not None and patron_user.is_active
                    and may_approve_rank(rank(patron_user), rank(user))):
                return patron_user, RecoveryTicket.RULE_PATRON

    if user.email and django_apps.is_installed("toto.socialhub"):
        from toto.socialhub.models import ReferenceRequest

        # The pipeline ties application to account by email (see
        # ReferenceRequest.save), so that is the join used here too.
        from django.db.models import F

        # nulls_last: Postgres puts NULLs FIRST under a plain DESC, so an
        # admin-created acceptance with no responded_at would outrank every
        # real one. A never-responded row loses to any dated row, then age
        # breaks the tie.
        ref = (
            ReferenceRequest.objects
            .filter(status="accepted", application__email=user.email)
            .exclude(referrer__user__isnull=True)
            .select_related("referrer__user")
            .order_by(F("responded_at").desc(nulls_last=True), "-created_at")
            .first()
        )
        if (ref is not None and ref.referrer.user.is_active
                and may_approve_rank(rank(ref.referrer.user), rank(user))):
            return ref.referrer.user, RecoveryTicket.RULE_REFERRER

    return None, RecoveryTicket.RULE_STAFF


def file_request(username: str, request=None):
    """File a recovery ticket for ``username``. Returns the ticket or None.

    None for every non-happy path — the caller's response must not depend on
    which one. Eligibility mirrors Django's ``PasswordResetForm.get_users``:
    active accounts with a usable password, so a federated identity (unusable
    password by construction, ``sso_client.views``) silently gets nothing here
    exactly as it gets no email there.
    """
    username = (username or "").strip()
    if not username:
        return None

    User = get_user_model()
    user = User.objects.filter(username=username).first()
    if user is None or not user.is_active or not user.has_usable_password():
        return None

    sweep_expired(user=user)

    approver, rule = resolve_approver(user)
    try:
        with transaction.atomic():
            ticket = RecoveryTicket.objects.create(
                user=user,
                approver=approver,
                approver_rule=rule,
                request_expires_at=timezone.now() + request_ttl(),
            )
    except IntegrityError:
        # one_pending_recovery_per_user: a ticket is already waiting. Filing
        # again must not multiply cards on the approver's profile.
        return None

    _audit(
        "PASSWORD_RECOVERY_REQUESTED",
        obj=user,
        description=f"Recovery ticket filed for '{user.username}' ({rule}).",
        actor_user=None,
        request=request,
        metadata={"ticket": str(ticket.pk), "rule": rule},
    )
    return ticket


def approve(ticket: RecoveryTicket, actor) -> uuid.UUID | None:
    """Mint the one-time link token for a pending ticket. None if not pending.

    The token is returned to the caller for ONE render and stored only as a
    hash. A staff member claiming a queue ticket becomes its approver, so the
    audit and the card both name a person rather than "somebody with a badge".
    """
    now = timezone.now()
    token = uuid.uuid4()
    with transaction.atomic():
        locked = (
            RecoveryTicket.objects.select_for_update().get(pk=ticket.pk)
        )
        if locked.status != RecoveryTicket.PENDING or locked.request_expired(now):
            return None
        locked.status = RecoveryTicket.APPROVED
        locked.approver = actor
        locked.responded_at = now
        locked.link_sha256 = hash_token(token)
        locked.link_expires_at = now + link_ttl()
        locked.save(update_fields=[
            "status", "approver", "responded_at", "link_sha256",
            "link_expires_at",
        ])
    _audit(
        "PASSWORD_RECOVERY_APPROVED",
        obj=ticket.user,
        description=f"Recovery ticket approved for '{ticket.user.username}'.",
        actor_user=actor,
        metadata={"ticket": str(ticket.pk), "rule": ticket.approver_rule},
    )
    return token


def reject(ticket: RecoveryTicket, actor) -> bool:
    """Close a pending ticket without minting anything."""
    now = timezone.now()
    with transaction.atomic():
        locked = (
            RecoveryTicket.objects.select_for_update().get(pk=ticket.pk)
        )
        if locked.status != RecoveryTicket.PENDING:
            return False
        locked.status = RecoveryTicket.REJECTED
        locked.approver = actor
        locked.responded_at = now
        locked.save(update_fields=["status", "approver", "responded_at"])
    _audit(
        "PASSWORD_RECOVERY_REJECTED",
        obj=ticket.user,
        description=f"Recovery ticket rejected for '{ticket.user.username}'.",
        actor_user=actor,
        metadata={"ticket": str(ticket.pk), "rule": ticket.approver_rule},
    )
    return True


def may_respond(user, ticket: RecoveryTicket) -> bool:
    """May ``user`` approve or reject this ticket?

    The named approver, or a staff member for a queue ticket (approver NULL) —
    and in BOTH cases only if they outrank the subject (see ``rank``). A named
    approver is re-checked rather than trusted: the ticket may have been filed
    when the subject was an ordinary member and the subject may have been
    promoted since.

    Never the subject: approving your own recovery would let a hijacked session
    mint itself a password-change link without knowing the current password.
    """
    if not user.is_authenticated or user.pk == ticket.user_id:
        return False
    if not may_approve_rank(rank(user), rank(ticket.user)):
        return False
    if ticket.approver_id is not None:
        return ticket.approver_id == user.pk
    return rank(user) >= RANK_STAFF


def redeem_ticket(token) -> RecoveryTicket | None:
    """The approved, unexpired ticket behind a link token, or None.

    Lookup is by hash, so nothing here compares secrets in a way that leaks
    timing — the database index either has the digest or it does not, and a
    wrong 122-bit UUID is not guessable enough for the difference to matter.
    """
    digest = hash_token(token)
    ticket = (
        RecoveryTicket.objects.filter(
            link_sha256=digest, status=RecoveryTicket.APPROVED,
        ).select_related("user").first()
    )
    if ticket is None:
        return None
    if ticket.link_expired():
        sweep_expired(user=ticket.user)
        return None
    return ticket


def mark_used(ticket: RecoveryTicket, request=None) -> bool:
    """Burn the link after a successful password change. Single-use is HERE:
    row lock, status check, then the flip — two racing redemptions cannot
    both win."""
    now = timezone.now()
    with transaction.atomic():
        locked = (
            RecoveryTicket.objects.select_for_update().get(pk=ticket.pk)
        )
        if locked.status != RecoveryTicket.APPROVED or locked.link_expired(now):
            return False
        locked.status = RecoveryTicket.USED
        locked.used_at = now
        locked.save(update_fields=["status", "used_at"])
    _audit(
        "PASSWORD_RECOVERY_USED",
        obj=ticket.user,
        description=f"Recovery link used — password set for '{ticket.user.username}'.",
        actor_user=None,
        request=request,
        metadata={"ticket": str(ticket.pk)},
    )
    return True


def sweep_expired(user=None) -> int:
    """Flip overdue tickets to EXPIRED and audit each once.

    Called opportunistically — filing a request, listing an approver's cards,
    redeeming a link — rather than from a beat schedule: a ticket nobody ever
    looks at again expiring silently costs nothing, and the moment anybody
    looks, the state they see is true. ``dedupe_key`` keeps the audit chain to
    one EXPIRED record per ticket no matter how many views sweep it.
    """
    now = timezone.now()
    from django.db.models import Q

    overdue = RecoveryTicket.objects.filter(
        Q(status=RecoveryTicket.PENDING, request_expires_at__lte=now)
        | Q(status=RecoveryTicket.APPROVED, link_expires_at__lte=now)
    )
    if user is not None:
        overdue = overdue.filter(user=user)

    count = 0
    for ticket in overdue.select_related("user"):
        with transaction.atomic():
            flipped = RecoveryTicket.objects.filter(
                pk=ticket.pk, status=ticket.status,
            ).update(status=RecoveryTicket.EXPIRED)
        if not flipped:
            continue
        count += 1
        _audit(
            "PASSWORD_RECOVERY_EXPIRED",
            obj=ticket.user,
            description=(
                f"Recovery ticket expired for '{ticket.user.username}' "
                f"({'link' if ticket.status == RecoveryTicket.APPROVED else 'request'})."
            ),
            # system=True, not actor_user=None: a sweep runs during whatever
            # page render touched it, and None would let the audit context
            # name that browsing user as the actor of an expiry they had no
            # part in.
            system=True,
            metadata={"ticket": str(ticket.pk)},
            dedupe_key=f"recovery-expired-{ticket.pk}",
        )
    return count


def tickets_for_approver(user):
    """The pending cards for ``user``'s own profile: theirs by name, plus the
    staff queue when they carry a badge. Sweeps first so a dead request never
    renders as actionable."""
    if not user.is_authenticated:
        return RecoveryTicket.objects.none()
    sweep_expired()
    from django.db.models import Q

    q = Q(approver=user)
    if rank(user) >= RANK_STAFF:
        q |= Q(approver__isnull=True)
    # The rank rule again, as SQL rather than a comprehension: the caller is a
    # profile plugin that asks `.exists()` before rendering
    # (`sso_core/plugins/profile_plugins.py:40`), so this must stay a
    # QuerySet. A card the viewer would be refused at is worse than a missing
    # one — it invites a click that 403s. `may_respond` remains the authority;
    # this only keeps the profile honest about it.
    viewer_rank = rank(user)
    if viewer_rank < RANK_SUPERUSER:
        # Never a subject who outranks the viewer.
        q &= ~Q(user__is_superuser=True)
    if viewer_rank < RANK_STAFF:
        # Below a badge, only ordinary members may be recovered at all —
        # which includes anybody in a protected group.
        q &= ~Q(user__is_staff=True)
        from django.conf import settings

        protected = getattr(settings, "RECOVERY_PROTECTED_GROUPS", ()) or ()
        if protected:
            q &= ~Q(user__groups__name__in=protected)
    return (
        RecoveryTicket.objects.filter(q, status=RecoveryTicket.PENDING)
        .exclude(user=user)
        .select_related("user")
        .distinct()
        .order_by("requested_at")
    )
