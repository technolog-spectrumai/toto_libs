"""Optional rewards for accepted work.

Kanban records the INTENT to pay and delegates the paying. That split is what
lets one deployment run the boards with no economy stack at all while another
credits a ledger for the same event, with no branch in the engine.

Why the imports here are inside methods
---------------------------------------

``toto.assets`` is not in ``INSTALLED_APPS`` on every host that installs
kanban. Importing ``toto.assets.services.assets`` at module scope imports
``toto.assets.models``, and Django raises

    RuntimeError: Model class toto.assets.models.Asset doesn't declare an
    explicit app_label and isn't in an application in INSTALLED_APPS

for a model whose app is absent — at import time, on every command, not merely
when a reward is paid. So the import is deferred into ``settle`` and guarded by
``apps.is_installed``. This is the same shape ``toto.assets.backend``'s own
``DjangoLedgerBackend.create_asset`` uses to reach ``toto.mint``.

(It also keeps the edge SOFT for ``scripts/check_package_graph.py``, which
treats function-level imports as optional integrations. That is now a
convenience rather than the reason: the wheel may declare the dependency since
2026-08-22. The app-registry constraint above is the one that still bites.)
"""

from __future__ import annotations

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.module_loading import import_string

from .models import (
    RewardGrant,
    RewardGrantState,
    RewardPolicy,
    RewardTrigger,
    SubmissionResolution,
)

#: Namespaced so a reference cannot collide with another subsystem's.
REFERENCE_PREFIX = "kanban:reward"


# ── backends ─────────────────────────────────────────────────────────────────

class RewardBackend:
    """Turn a recorded grant into an actual movement of value.

    Stateless: one is constructed per call, so nothing may be cached on self.
    """

    def settle(self, grant: RewardGrant) -> None:
        raise NotImplementedError


class NullRewardBackend(RewardBackend):
    """The default. No economy on this host, so nothing moves.

    Marks the grant ``skipped`` rather than deleting it: "what would we have
    paid, had there been anywhere to pay it" is a question worth being able to
    answer, and a host can adopt a real backend later and see the backlog.
    """

    def settle(self, grant: RewardGrant) -> None:
        _finish(grant, RewardGrantState.SKIPPED,
                "No reward backend configured on this host.")


class AssetsRewardBackend(RewardBackend):
    """Credit the recipient on the ``toto.assets`` ledger.

    Every failure marks the grant ``failed`` with the reason and returns. It
    must never raise into the caller: this runs on ``transaction.on_commit``,
    after the consensus result is already durable, and a reward that cannot be
    paid is not a reason to lose a review.
    """

    def settle(self, grant: RewardGrant) -> None:
        from django.apps import apps  # noqa: PLC0415

        if not apps.is_installed("toto.assets"):
            _finish(grant, RewardGrantState.SKIPPED,
                    "toto.assets is not installed on this host.")
            return

        from toto.assets.models import Asset, LedgerAccount  # noqa: PLC0415
        from toto.assets.services.assets import transfer_asset  # noqa: PLC0415

        try:
            asset = Asset.objects.filter(unit_name=grant.asset_code).first()
            if asset is None:
                asset = Asset.objects.filter(code=grant.asset_code).first()
            if asset is None:
                _finish(grant, RewardGrantState.FAILED,
                        f"No asset with symbol {grant.asset_code!r}.")
                return

            payer = LedgerAccount.objects.filter(
                code=grant.funding_account_code).first()
            if payer is None:
                _finish(grant, RewardGrantState.FAILED,
                        f"No funding account {grant.funding_account_code!r}.")
                return

            recipient_account = self._account_for(grant.recipient, LedgerAccount)
            if recipient_account is None:
                _finish(grant, RewardGrantState.FAILED,
                        f"{grant.recipient} has no ledger account.")
                return

            from decimal import Decimal  # noqa: PLC0415
            from toto.assets.models import from_base_units  # noqa: PLC0415

            transfer_asset(
                asset=asset,
                sender_account=payer,
                receiver_account=recipient_account,
                amount=from_base_units(grant.amount_base_units, asset.decimals),
                reference=grant.reference,
                description=f"kanban reward ({grant.policy.get_trigger_display()})",
            )
        except Exception as exc:  # noqa: BLE001 - see the docstring
            # An exhausted funding account lands here, and lands here on
            # purpose: the acceptance stands, the payment is marked failed with
            # its reason, and re-running settle() retries it safely because the
            # reference has not been consumed.
            _finish(grant, RewardGrantState.FAILED, str(exc)[:500])
            return

        _finish(grant, RewardGrantState.SETTLED, "")

    @staticmethod
    def _account_for(person, LedgerAccount):
        """This person's wallet, by the platform's own convention."""
        user_id = getattr(person, "user_id", None)
        if user_id is None:
            return None
        return LedgerAccount.objects.filter(user_id=user_id, active=True).first()


def get_backend() -> RewardBackend:
    """The configured backend. Mirrors ``toto.assets.backend.get_backend``."""
    path = getattr(settings, "KANBAN_REWARD_BACKEND",
                   "toto.kanban.rewards.NullRewardBackend")
    return import_string(path)()


# ── recording ────────────────────────────────────────────────────────────────

def applicable_policies(mission):
    """Active policies for this mission: its own, plus its campaign's."""
    return RewardPolicy.objects.filter(active=True).filter(
        Q(mission=mission)
        | Q(campaign_id=mission.campaign_id, mission__isnull=True)
    )


def reference_for(policy, recipient, *, submission=None, review=None) -> str:
    """A stable, unique name for one intended payment.

    Built from primary keys rather than a counter or a timestamp so that the
    SAME logical payment computes the SAME reference on a retry — which is the
    entire basis of the idempotency, here and at the ledger.
    """
    subject = f"r{review.pk}" if review is not None else f"s{submission.pk}"
    return f"{REFERENCE_PREFIX}:{policy.pk}:{subject}:p{recipient.pk}"


def record_grant(policy, recipient, *, submission=None, review=None):
    """Write the intent to pay. Idempotent on ``reference``."""
    reference = reference_for(
        policy, recipient, submission=submission, review=review)
    try:
        with transaction.atomic():
            return RewardGrant.objects.create(
                policy=policy,
                submission=submission,
                review=review,
                recipient=recipient,
                asset_code=policy.asset_code,
                amount_base_units=policy.amount_base_units,
                funding_account_code=policy.funding_account_code,
                reference=reference,
            )
    except IntegrityError:
        # Already recorded. A second resolve, a retried task, a double click —
        # all converge here, which is the point.
        return RewardGrant.objects.get(reference=reference)


def on_submission_resolved(submission) -> list[RewardGrant]:
    """Record every grant this resolution earns, then settle after commit.

    Called from inside ``work.resolve``'s transaction, so the grant ROWS roll
    back with the resolution if anything fails. Settlement itself is deferred
    to ``transaction.on_commit``: money must not move for a review that never
    became durable, and a ledger failure must not undo one that did.
    """
    mission = submission.task.mission
    grants: list[RewardGrant] = []

    for policy in applicable_policies(mission):
        if policy.trigger == RewardTrigger.SUBMISSION_ACCEPTED:
            if submission.resolution == SubmissionResolution.ACCEPTED:
                grants.append(record_grant(
                    policy, submission.submitted_by, submission=submission))

        elif policy.trigger == RewardTrigger.REVIEW_RESOLVED:
            # Paid to every reviewer who took part, WHICHEVER WAY THEY VOTED.
            # Paying only the majority would pay people to agree, and the one
            # thing a reward must never influence is the verdict.
            for review in submission.reviews.select_related("reviewer"):
                grants.append(record_grant(
                    policy, review.reviewer,
                    submission=submission, review=review))

    if grants:
        pending = [g.pk for g in grants if g.state == RewardGrantState.PENDING]
        if pending:
            transaction.on_commit(lambda: settle(pending))
    return grants


def settle(grant_pks) -> None:
    """Settle recorded grants. Safe to call again on the same ids."""
    backend = get_backend()
    for grant in RewardGrant.objects.filter(
            pk__in=list(grant_pks), state=RewardGrantState.PENDING):
        backend.settle(grant)


def retry_failed(*, limit: int = 100) -> int:
    """Re-attempt failed settlements. The reference makes this safe."""
    stuck = list(RewardGrant.objects
                 .filter(state=RewardGrantState.FAILED)
                 .values_list("pk", flat=True)[:limit])
    RewardGrant.objects.filter(pk__in=stuck).update(
        state=RewardGrantState.PENDING, detail="")
    settle(stuck)
    return len(stuck)


def _finish(grant, state, detail) -> None:
    grant.state = state
    grant.detail = detail
    grant.settled_at = timezone.now()
    grant.save(update_fields=["state", "detail", "settled_at"])
