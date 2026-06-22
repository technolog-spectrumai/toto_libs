"""Top-level orchestrator: pasted text → persisted IngestProposal."""

from toto.bento.models import BentoCategory

from . import catalog as catalog_svc
from . import detection as detection_svc
from . import proposal as proposal_svc
from . import validation


def build_proposal_dict(text):
    """Run the full deterministic pipeline and return ``(proposal, summary)``.

    Pure function (no DB writes) so it's easy to unit-test. ``generate`` persists.
    """
    catalog_entries, catalog_meta = catalog_svc.build_catalog()
    known_slugs = set(BentoCategory.objects.values_list("slug", flat=True))
    detection = detection_svc.detect(text, catalog_entries, known_slugs)
    proposal = proposal_svc.assemble_from_catalog(detection, catalog_entries)

    summary = validation.summarize(proposal)
    summary["catalog"] = catalog_meta
    summary["ner_available"] = detection.ner_available
    summary["spacy_available"] = detection.spacy_available
    return proposal, summary


def generate(text, user=None):
    """Build a proposal and persist it as a ready :class:`IngestProposal`."""
    from ..models import IngestProposal

    proposal, summary = build_proposal_dict(text)
    return IngestProposal.objects.create(
        status=IngestProposal.STATUS_READY,
        source_text=text,
        proposal=proposal,
        summary=summary,
        created_by=(user if getattr(user, "is_authenticated", False) else None),
    )
