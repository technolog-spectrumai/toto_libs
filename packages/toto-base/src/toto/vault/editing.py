"""The rules every rich editor obeys, in one place.

Four editors write vault files — primula (workbooks), memo (decks), cyprian
(documents) and the ACE editor (text). Before this module each had its own
answer to the same five questions, and the answers had drifted: cyprian took a
lock and checked a base hash, primula had the server half of both but a client
that never armed them, memo had the lot, and the ACE editor had none of it and
silently overwrote whatever it landed on.

The five questions, and who answers them:

=========================  =====================================================
may this person edit?      the subscription entitlement for the app
is anybody else in it?     ``toto.vault.locks`` — one holder, heartbeat, 423
did the file move?         the ``base_hash`` precondition — 409, work rescued
what does the save cost?   ``toto.quota`` — a usage event, then a charge
what is kept?              ``toto.vault.versions`` — one version per save
=========================  =====================================================

## The door is where money and plan are decided, never the save

A save is never refused for money. That is a deliberate inversion of the usual
order and the reason is narrow: refusing a save loses work that exists only in a
browser tab, and this platform has no self-service top-up, so the loss is not
recoverable without an admin. Primula's original metric carried a comment saying
exactly this, and it is why that metric was seeded ``TRACK`` and priced at
nothing.

So the checks move to the *entrance*. :func:`door_for` runs on the GET that
renders an editor, and answers with a :class:`Door` that is one of:

* **open and writable** — the ordinary case;
* **open, read-only** — no funds, an unpaid levy, an encrypted file, or somebody
  else holding the lock. The document renders, the Save button does not, and the
  banner says which of those it is;
* **closed** — the plan does not include this editor. 402, and the caller
  renders the plans page.

The consequence worth stating: **you can never be refused a save for a document
you were allowed to open.** If the wallet empties mid-session the save still
lands and the response carries a warning; the read-only door appears the next
time the file is opened, when nothing is at stake.

## Saving is deliberate here

There is no autosave behind this module and the editors that call it do not have
one. One save is one version and one charge, so a save has to be an act somebody
chose — a debounce firing every two seconds would bill by the minute and bury
the two versions anyone cared about under thirty they never asked for. The
safety net is the other way round: the lock is held by heartbeat for as long as
the tab is open, and the editors guard ``beforeunload``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.http import JsonResponse
from django.utils.translation import gettext as _

from . import locks, versions

log = logging.getLogger("toto.vault.editing")

#: HTTP 423. A save refused because somebody else holds the lock. Not 403 — the
#: caller does hold the right — and not 409, which would invite a retry that
#: cannot succeed until the holder leaves.
HTTP_LOCKED = 423

#: HTTP 402. The plan does not include this editor.
PAYMENT_REQUIRED = 402


@dataclass(frozen=True)
class Door:
    """Whether this person may open this editor, and in what mode.

    ``open`` is about the page; ``writable`` is about the Save button. They are
    separate because "you may look at your own document but not change it today"
    is a real and common state — an empty wallet, an unpaid levy, a colleague
    already in the file — and answering it with a 402 page would be telling
    somebody they cannot reach their own work.
    """

    open: bool = True
    writable: bool = True
    #: What the caller should return when ``open`` is False.
    status: int = 200
    #: A machine-readable cause. "" when the door is fully open.
    reason: str = ""
    #: One sentence for the person, already translated.
    message: str = ""
    #: The username of whoever holds the lock, when ``reason`` is "locked".
    locked_by: str = ""
    entitlement: str = ""

    def as_context(self) -> dict:
        """The template half — what an editor page needs to render the banner."""
        return {
            "can_edit": self.writable,
            "edit_reason": self.reason,
            "edit_message": self.message,
            "locked_by": self.locked_by,
        }


def door_for(user, vault_file=None, *, entitlement: str,
             metric_code: str = "", policy_model=None) -> Door:
    """Decide whether this editor may open for this person, and in what mode.

    Order matters and runs from the most fundamental refusal outwards: a host
    with editing switched off, then the plan, then the file itself, then the
    lock, then money. The first one that answers wins, so the message names the
    thing the user can actually act on.
    """
    from .models import file_edits_allowed

    # 1. The host. `faros` sets VAULT_FILE_EDITS=False and means it; no plan and
    #    no balance changes that answer.
    if not file_edits_allowed():
        return Door(writable=False, reason="host",
                    message=_("File editing is switched off on this host."),
                    entitlement=entitlement)

    if not getattr(user, "is_authenticated", False):
        return Door(open=False, writable=False, status=PAYMENT_REQUIRED,
                    reason="anonymous",
                    message=_("Sign in to edit."), entitlement=entitlement)

    # 2. The plan. This is the only refusal that CLOSES the door: an editor
    #    whose every save answers 402 is worse than an honest plans page.
    if not _entitled(user, entitlement):
        return Door(open=False, writable=False, status=PAYMENT_REQUIRED,
                    reason="subscription-required",
                    message=_("Editing is not included in your plan."),
                    entitlement=entitlement)

    # 3. The file. An encrypted file has no plaintext to hand an editor.
    if vault_file is not None and getattr(vault_file, "is_encrypted", False):
        return Door(writable=False, reason="encrypted",
                    message=_("This file is encrypted. Decrypt it to edit it."),
                    entitlement=entitlement)

    # 4. The lock — the whole point of which is that it is visible before the
    #    user has typed anything, not after they try to save.
    if vault_file is not None and not locks.may_write(vault_file, user):
        held = locks.holder_of(vault_file)
        who = held.holder.get_username() if held else ""
        return Door(writable=False, reason="locked",
                    message=_("%(who)s is editing this file right now.")
                            % {"who": who or _("Somebody else")},
                    locked_by=who, entitlement=entitlement)

    # 5. Money, last, and never fatal to the page.
    money = _affordable(user, entitlement, metric_code, policy_model)
    if money is not None:
        return money

    return Door(entitlement=entitlement)


def _priced_app(metric_code: str) -> str:
    """The app_label a tariff is looked up under: the metric's own prefix.

    Deliberately NOT the entitlement. ``price_for`` resolves a rate card by
    ``items__metric__app_label``, and the two names come apart the moment a
    bridged document is edited — a kanban wiki page is covered by the *kanban*
    plan but is still a ``cyprian.save``, and pricing it under "kanban" would
    look for a rate card that has no item for this metric and silently charge
    nothing.
    """
    return metric_code.split(".", 1)[0]


def _entitled(user, entitlement: str) -> bool:
    """Ask `toto.subscriptions`, and treat its absence as "yes".

    A host that does not ship subscriptions sells nothing, so gating there would
    refuse everybody for a plan they cannot buy.
    """
    if not entitlement:
        return True
    from django.apps import apps as django_apps

    if not django_apps.is_installed("toto.subscriptions"):
        return True

    # Whether the gate MIDDLEWARE is installed is the host's switch for
    # enforcement — `toto.subscriptions` itself is installed unconditionally,
    # because Stations has a required FK to SubscriptionPlan, and
    # `BUILD_SUBSCRIPTIONS_ENFORCE` decides only whether the middleware is
    # added. Asking `is_entitled` directly would therefore paywall the editor
    # page on a host that has deliberately left every write open, which is the
    # one place a door must not disagree with the gate.
    from django.conf import settings

    if not any("SubscriptionGateMiddleware" in m
               for m in getattr(settings, "MIDDLEWARE", ())):
        return True

    from toto.subscriptions.gate import is_entitled

    return is_entitled(user, entitlement)


def _affordable(user, entitlement: str, metric_code: str, policy_model) -> Door | None:
    """A read-only Door when this save could not be paid for, else None.

    Both refusals here are 402-shaped in the quota package, and both are
    downgraded to a read-only page: the user keeps their file, and the sentence
    tells them which of the two it is, because the fixes are different — an
    unpaid levy is settled on the fees page and an empty balance is topped up.
    """
    if not metric_code:
        return None
    from toto.quota.api import InArrears, QuotaExceeded, check_quota
    from toto.quota.charge import InsufficientFunds, check_funds, price_for

    if policy_model is not None:
        try:
            check_quota(policy_model, metric_code, 1, user)
        except InArrears as exc:
            return Door(writable=False, reason="in-arrears",
                        message=str(exc), entitlement=entitlement)
        except QuotaExceeded as exc:
            # Only reachable if somebody switched this metric to BLOCK. The
            # seeded policy is TRACK precisely so this does not happen mid-work.
            return Door(writable=False, reason="quota-exceeded",
                        message=str(exc), entitlement=entitlement)
    try:
        check_funds(user, price_for(user, _priced_app(metric_code)), metric_code, 1)
    except InsufficientFunds as exc:
        return Door(writable=False, reason="insufficient-funds",
                    message=str(exc), entitlement=entitlement)
    return None


def closed_response(request, door: Door):
    """What a view returns when :attr:`Door.open` is False.

    Reuses the subscription gate's own refusal so a paywalled editor looks
    exactly like every other paywalled write on the platform — same page, same
    JSON shape, same plans link — instead of growing a second, thinner one that
    drifts. The gate would produce this itself for the save; the editor page is
    a GET, which the gate deliberately lets through, so the view has to ask.
    """
    from django.apps import apps as django_apps

    if door.reason == "subscription-required" and \
            django_apps.is_installed("toto.subscriptions"):
        from toto.subscriptions.gate import SubscriptionGateMiddleware

        return SubscriptionGateMiddleware(lambda _r: None).refuse(
            request, door.entitlement)

    return JsonResponse({"error": door.message, "reason": door.reason},
                        status=door.status)


# --------------------------------------------------------------------------- #
# The save path                                                               #
# --------------------------------------------------------------------------- #

def refuse_if_locked(vault_file, user, *, noun: str = "file"):
    """423 when somebody else is in this file, else None.

    The first line of the save, before the body is even parsed: a save that
    should never have been attempted must not be able to fail halfway.
    """
    if locks.may_write(vault_file, user):
        return None
    held = locks.holder_of(vault_file)
    who = held.holder.get_username() if held else _("Somebody else")
    return JsonResponse(
        {"error": _("%(who)s is editing this %(noun)s.")
                  % {"who": who, "noun": noun},
         "locked_by": who if held else ""},
        status=HTTP_LOCKED)


def refuse_if_stale(vault_file, base_hash, *, body: bytes, author, noun: str = "file"):
    """409 when the file moved under this buffer, keeping the loser's work.

    Refusing alone is what a user experiences as "it lost my paragraph". These
    formats cannot be merged — a document's whole body is one CDATA line and a
    workbook's whole snapshot is one JSON line — so the honest answer is two
    versions and a human, which is where every other document product landed.
    """
    if not base_hash or not vault_file.content_hash:
        return None
    if base_hash == vault_file.content_hash:
        return None

    rescued = None
    try:
        rescued = versions.save_conflicting_draft(
            vault_file, body=body, author=author)
    except Exception:                                   # noqa: BLE001
        log.exception("vault.editing: could not rescue a losing draft for %s",
                      vault_file.pk)                    # never a 409 turned 500

    return JsonResponse(
        {"error": _("This %(noun)s changed somewhere else since you opened it. "
                    "Your work was kept as a version so nothing is lost.")
                  % {"noun": noun},
         "content_hash": vault_file.content_hash,
         "kept_as_version": rescued.number if rescued else None},
        status=409)


def settle(vault_file, user, *, metric_code: str, event_model=None,
           policy_model=None, label: str = "") -> dict:
    """After the bytes are written: keep a version, count it, charge for it.

    Called only on a save that actually landed, so a failed save is never a
    charged one, and in this order for a reason the aralia views state: the
    charge follows the usage event and happens only when that event was really
    written, so a duplicate cannot bill twice.

    Never raises. An empty balance produces a warning in the returned dict, not
    a refusal — see this module's docstring for why that is the whole design.
    """
    out: dict = {}

    # 1. The version. Every save is one, which is what makes an editor's history
    #    worth opening — and the conflict rescue above already relies on it.
    try:
        version = versions.save_version(vault_file, author=user, label=label)
        out["version"] = getattr(version, "number", None)
    except Exception:                                   # noqa: BLE001
        log.exception("vault.editing: could not version %s", vault_file.pk)

    if not metric_code or event_model is None:
        return out

    from toto.quota.api import record_usage
    from toto.quota.charge import InsufficientFunds, charge, price_for

    source = {"source_type": "vault.VaultFile", "source_id": str(vault_file.pk),
              "source_label": vault_file.title}

    # 2. The count. `record_usage` never raises: it returns the event, or None
    #    for a duplicate or a write failure, deliberately indistinguishable.
    #    No idempotency key — saving twice IS two saves, which is the point.
    recorded = record_usage(event_model, metric_code, 1, user, **source)

    # 3. The charge, and only if the count was written.
    if recorded is not None:
        try:
            charge(user, price_for(user, _priced_app(metric_code)), metric_code, 1,
                   **source)
        except InsufficientFunds as exc:
            # The save stands. Said out loud, because a silent free save teaches
            # people the meter is broken.
            out["billing_warning"] = str(exc)
        except Exception:                               # noqa: BLE001
            log.exception("vault.editing: charge failed for %s on %s",
                          metric_code, vault_file.pk)
    return out
