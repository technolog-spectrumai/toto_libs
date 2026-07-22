"""Top-level orchestrator: pasted text → persisted IngestProposal."""

from toto.bento.models import BentoCategory

from . import catalog as catalog_svc
from . import detection as detection_svc
from . import proposal as proposal_svc
from . import validation


def build_proposal_dict(text, *, include_existing_nodes=False):
    """Run the full deterministic pipeline and return ``(proposal, summary)``.

    Pure function (no DB writes) so it's easy to unit-test. ``generate`` persists.
    ``include_existing_nodes`` lets relationships link to existing graph nodes
    named anywhere in ``text`` (not just within the same sentence).
    """
    catalog_entries, catalog_meta = catalog_svc.build_catalog()
    known_slugs = set(BentoCategory.objects.values_list("slug", flat=True))
    detection = detection_svc.detect(text, catalog_entries, known_slugs)
    proposal = proposal_svc.assemble_from_catalog(
        detection, catalog_entries, include_existing_nodes=include_existing_nodes
    )

    summary = validation.summarize(proposal)
    summary["catalog"] = catalog_meta
    summary["ner_available"] = detection.ner_available
    summary["spacy_available"] = detection.spacy_available
    return proposal, summary


def generate(text, user=None):
    """Back-compat entry point: delegate to the default (deterministic) strategy.

    New code should select a strategy explicitly via
    ``toto.ingestor.services.strategies.IngestStrategy``.
    """
    from .strategies import IngestStrategy

    return IngestStrategy.get("deterministic").run(text, user=user)
