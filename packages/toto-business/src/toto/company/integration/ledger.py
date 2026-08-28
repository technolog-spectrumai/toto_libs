"""The seam between a Company and its chain.

`toto.ledger` knows nothing about companies — it takes a `scope_type` and a
`scope_uid` and never looks them up. This module is the only place that knows
those two strings mean a Company, which is what keeps the engine reusable and
keeps the dependency pointing one way.

**Exactly one chain per Company**, opened on demand and idempotently, so
`company_ledger()` is safe to call from a view, a migration backfill or a test
without anyone tracking whether it has run.
"""

from __future__ import annotations

from django.db import transaction

from toto.company.models import Company
from toto.ledger.models import LedgerKind
from toto.ledger.services import chain

#: The chain every Company gets. One key, because the brief says one chain.
ACTIONS_KEY = "company-actions"

#: How a ledger names a Company. A string, never an import: the ledger app must
#: keep working on a host where this app is not installed.
SCOPE_TYPE = "company.company"


def company_ledger(company: Company, *, actor=None):
    """This Company's chain, opening it with its genesis block if new."""
    return chain.open_ledger(
        key=ACTIONS_KEY,
        name=f"{company.name} — company actions",
        kind=LedgerKind.COMPANY,
        scope_type=SCOPE_TYPE,
        scope_uid=company.uid,
        description="Recorded actions of this company, in the order they were recorded.",
        actor=actor,
    )


def existing_ledger(company: Company):
    """The chain if it exists, without opening one. For read-only pages."""
    from toto.ledger.models import Ledger

    return Ledger.objects.filter(
        scope_type=SCOPE_TYPE, scope_uid=company.uid, key=ACTIONS_KEY,
    ).first()


def company_for(ledger):
    """The Company a chain belongs to, or None if it has outlived one."""
    if ledger.scope_type != SCOPE_TYPE or not ledger.scope_uid:
        return None
    return Company.objects.filter(uid=ledger.scope_uid).first()


def backfill():
    """Open a chain for every Company that has none. Idempotent."""
    opened = 0
    for company in Company.objects.all().iterator():
        if existing_ledger(company) is None:
            company_ledger(company)
            opened += 1
    return opened


# ---------------------------------------------------------------------------
# Recording an Action
# ---------------------------------------------------------------------------


def company_facts(company: Company) -> dict:
    """The complete Company-fact payload frozen into a block.

    Complete on purpose: the block has to stay readable when the Company row
    has moved on, been renamed, or gone. A block that stored only an id would
    be a pointer into a mutable table, which is the opposite of a record.
    """
    from toto.company.models import ShareHolding

    holdings = (
        ShareHolding.objects
        .filter(share_class__company=company, until__isnull=True)
        .select_related("party", "share_class")
        .order_by("share_class__slug", "party__name")
    )
    return {
        "uid": str(company.uid),
        "name": company.name,
        "slug": company.slug,
        "form": company.form,
        "legal_form_label": company.legal_form_label,
        "registry_no": company.registry_no,
        "tax_no": company.tax_no,
        "statistical_no": company.statistical_no,
        "seat": company.seat,
        "share_capital": company.share_capital,
        "capital_currency": company.capital_currency,
        "departments": [
            {
                "name": department.name,
                "slug": department.slug,
                "parent": department.parent.slug if department.parent_id else "",
                "head": department.head.name if department.head_id else "",
            }
            for department in company.departments.filter(active=True)
            .select_related("parent", "head").order_by("slug")
        ],
        "members": [
            {
                "person": membership.person.display_name,
                "job_title": membership.job_title,
                "primary_department": (
                    membership.primary_department.slug
                    if membership.primary_department_id else ""
                ),
            }
            for membership in company.memberships.filter(active=True)
            .select_related("person", "primary_department")
            .order_by("person__display_name")
        ],
        "shareholders": [
            {
                "party": holding.party.name,
                "share_class": holding.share_class.slug,
                "units": holding.units,
                "votes": holding.votes,
            }
            for holding in holdings
        ],
    }


def action_payload(action) -> dict:
    """One Action, plus the Company as it stood when it was recorded."""
    return {
        "action": {
            "uid": str(action.uid),
            "title": action.title,
            "body": action.body,
            "kind": action.kind,
            "recorded_at": action.recorded_at,
        },
        "company": company_facts(action.company),
    }


@transaction.atomic
def record_action(action, *, actor=None, signer=None):
    """Freeze an Action and append it. One transaction, or neither happens.

    The Action is marked recorded and the block is written together: a draft
    that says "recorded" with no block, or a block whose draft still says
    "draft", would each be a lie the next page renders.
    """
    from toto.company.models import ActionStatus

    if action.status != ActionStatus.DRAFT:
        raise ValueError("Only a draft action can be recorded.")

    ledger = company_ledger(action.company, actor=actor)
    action.mark_recorded()
    entry = chain.append(
        ledger=ledger,
        payload=action_payload(action),
        actor=actor,
        occurred_at=action.recorded_at,
        source_type="company.action",
        source_uid=action.uid,
        source_ref=action.title,
        signer=signer,
    )
    action.block_uid = entry.uid
    action.save(update_fields=["block_uid"], _allow_update=True)
    return entry


def verify_company_ledger(company: Company):
    """Walk this Company's chain. Reads only."""
    ledger = existing_ledger(company)
    if ledger is None:
        return chain.Verification(True, 0, None, "This company has no chain yet.")
    return chain.verify(ledger)
