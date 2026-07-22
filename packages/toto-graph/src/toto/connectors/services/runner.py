"""Run orchestration: extract → archive → transform → persist → (trusted) apply.

``execute_run`` owns every status transition and error record on the
:class:`~toto.connectors.models.ConnectorRun`; callers (celery task, inline
fallback) never need their own error handling. Neo4j writes happen only inside
``ingestor.services.apply`` → ``bento.graph_service``.
"""

import logging

from django.utils import timezone

from toto.celery_utils import celery_available
from toto.ingestor.services import apply as apply_svc
from toto.ingestor.services import catalog as catalog_svc
from toto.ingestor.services import validation
from toto.ingestor.services.approval import approve_all_valid
from toto.ingestor.services.strategies.base import persist_review

from ..models import ConnectorRun
from . import archive, transform
from .extract import get_extractor

logger = logging.getLogger(__name__)


class RunnerError(RuntimeError):
    pass


class RunInProgress(RunnerError):
    pass


def create_run(connector, *, triggered, user=None):
    """Create a PENDING run; refuses to stack runs for one connector."""
    if connector.has_run_in_flight():
        raise RunInProgress(
            f"Connector '{connector.slug}' already has a run pending or running."
        )
    return ConnectorRun.objects.create(
        connector=connector,
        triggered=triggered,
        created_by=user,
    )


def dispatch_run(run):
    """Queue the run on celery when a worker is up, else execute inline.

    Returns True when queued asynchronously.
    """
    from ..tasks import run_connector_task

    if celery_available():
        async_result = run_connector_task.delay(run.pk)
        ConnectorRun.objects.filter(pk=run.pk).update(task_id=async_result.id or "")
        return True
    execute_run(run.pk)
    return False


def _vault_session(connector):
    """An unlocked Gervazy session when the HTTP connector needs one, else None."""
    from toto.api.models import ApiConnector

    if connector.http_connector.auth_type == ApiConnector.AUTH_NONE:
        return None
    from toto.sabbia import vault

    try:
        return vault.open_session()
    except vault.VaultUnavailable as exc:
        raise RunnerError(
            "Connector credentials are locked: the system strongbox is not "
            "available. Set SABBIA_VAULT_PASSWORD and run "
            "`manage.py connectors_init_vault` on this process's host. "
            f"({exc})"
        ) from exc


def execute_run(run_id):
    """Execute one run end to end, recording every outcome on the run row."""
    run = (
        ConnectorRun.objects.select_related("connector", "connector__http_connector")
        .filter(pk=run_id)
        .first()
    )
    if run is None:
        logger.warning("connectors: run %s vanished before execution", run_id)
        return
    if run.status != ConnectorRun.STATUS_PENDING:
        logger.info("connectors: run %s already %s — skipping", run_id, run.status)
        return

    connector = run.connector
    now = timezone.now()
    run.status = ConnectorRun.STATUS_RUNNING
    run.started_at = now
    run.save(update_fields=["status", "started_at"])
    type(connector).objects.filter(pk=connector.pk).update(last_run_at=now)

    try:
        extractor = get_extractor(connector.extractor_kind)
        result = extractor.extract(
            data_connector=connector, vault_session=_vault_session(connector)
        )
        run.raw_payload_file = archive.archive_payload(run, result)
        run.request_log = [page.audit_entry() for page in result.pages]
        run.save(update_fields=["raw_payload_file", "request_log"])

        entries, catalog_meta = catalog_svc.build_catalog()
        proposal, tstats = transform.transform(
            result.records, connector.mapping_spec or {}, entries=entries
        )

        if not proposal["nodes"] and not proposal["relationships"]:
            run.status = ConnectorRun.STATUS_EMPTY
            run.stats = {**result.stats, **tstats, "catalog": catalog_meta}
            return

        validation.revalidate(proposal)
        summary = validation.summarize(proposal)
        summary["strategy"] = "connector"
        summary["catalog"] = catalog_meta
        summary["connector"] = {"slug": connector.slug, "run_id": run.pk}
        run.proposal = persist_review(
            text=f"[connector:{connector.slug}] run #{run.pk}",
            proposal=proposal,
            summary=summary,
            user=run.created_by or connector.owner,
        )
        run.stats = {
            **result.stats,
            **tstats,
            "catalog": catalog_meta,
            "new_nodes": summary.get("new_nodes", 0),
            "existing_nodes": summary.get("existing_nodes", 0),
            "relationships": summary.get("relationships", 0),
            "validation_errors": summary.get("errors", 0),
        }

        if connector.trusted:
            approve_all_valid(run.proposal)
            _result, errors = apply_svc.run(run.proposal)
            if errors:
                run.status = ConnectorRun.STATUS_FAILED
                run.error = "\n".join(errors)
            else:
                run.status = ConnectorRun.STATUS_APPLIED
        else:
            run.status = ConnectorRun.STATUS_REVIEW
    except Exception as exc:  # noqa: BLE001 — the run row is the error surface
        logger.exception("connectors: run %s failed", run_id)
        run.status = ConnectorRun.STATUS_FAILED
        run.error = str(exc)
    finally:
        run.finished_at = timezone.now()
        run.save()
