"""Mirror terminal IngestProposal outcomes onto their ConnectorRun.

Reviewers approve/apply connector proposals through the untouched ingestor
endpoints, which know nothing about runs. This post_save hook is how a human
apply (or a failed one) lands back on the run's audit trail.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from toto.ingestor.models import IngestProposal

from .models import ConnectorRun


@receiver(post_save, sender=IngestProposal, dispatch_uid="connectors_sync_run")
def sync_connector_run(sender, instance, **kwargs):
    if instance.status not in (
        IngestProposal.STATUS_APPLIED,
        IngestProposal.STATUS_FAILED,
    ):
        return
    # Cheap for ordinary ingestor proposals: connector_runs is empty.
    for run in instance.connector_runs.filter(
        status__in=[ConnectorRun.STATUS_REVIEW, ConnectorRun.STATUS_FAILED]
    ):
        run.sync_from_proposal()
