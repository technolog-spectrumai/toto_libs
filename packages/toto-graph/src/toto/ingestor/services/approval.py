"""Approval helpers shared by the review views and non-HTTP callers.

Extracted from ``ingestor.views`` so background workers (e.g. a trusted
connector run in ``toto.connectors``) can approve-and-apply a proposal without
importing view/HTTP concerns. The never-auto-approve-invalid rule lives here,
single-sourced.
"""

from .validation import revalidate, summarize


def save_edits(proposal_model):
    """Re-validate the whole proposal, refresh the summary, persist."""
    revalidate(proposal_model.proposal)
    proposal_model.summary = {**(proposal_model.summary or {}), **summarize(proposal_model.proposal)}
    proposal_model.save(update_fields=["proposal", "summary", "updated_at"])


def bulk_set_approval(proposal_model, approval):
    """Set ``approval`` on every non-error node/relationship, then persist.

    Re-validates against the live Bento templates first, applies the state only
    to elements that aren't in error, and refreshes the summary. Shared by the
    Select-all toggle and the ``approve_all`` apply path.
    """
    proposal = proposal_model.proposal
    revalidate(proposal)
    elements = (proposal.get("nodes") or []) + (proposal.get("relationships") or [])
    for el in elements:
        # Never auto-approve an element that fails validation; clearing or
        # rejecting is always safe, so those apply to every element.
        if approval == "approved" and (el.get("validation") or {}).get("status") == "error":
            continue
        el["approval"] = approval
    save_edits(proposal_model)


def approve_all_valid(proposal_model):
    """Mark every non-error node/relationship as approved (for ``approve_all``)."""
    bulk_set_approval(proposal_model, "approved")
