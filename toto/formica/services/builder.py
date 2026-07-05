"""Assemble a cycle's collected ops into a reviewable FormicaProposal."""

from ..models import FormicaProposal
from . import apply as apply_svc


def build_proposal(ctx):
    """Validate the ops against the live graph, persist as READY, and expire
    older still-unreviewed proposals of the same colony (superseded)."""
    ops = apply_svc.revalidate(list(ctx.ops))
    proposal = FormicaProposal.objects.create(
        colony=ctx.colony,
        cycle=ctx.cycle,
        status=FormicaProposal.STATUS_READY,
        ops={"ops": ops},
        summary=apply_svc.summarize(ops),
    )
    FormicaProposal.objects.filter(
        colony=ctx.colony, status=FormicaProposal.STATUS_READY
    ).exclude(pk=proposal.pk).update(status=FormicaProposal.STATUS_EXPIRED)
    return proposal
