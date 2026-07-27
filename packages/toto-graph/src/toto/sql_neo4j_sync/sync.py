from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from .models import GraphChangeEvent


def incremental_sync_enabled():
    return bool(getattr(settings, "RAVIOLI_INCREMENTAL_SYNC", True))


def enqueue_graph_event(action, graph_label="", model_path="", object_uuid="", payload=None):
    if not incremental_sync_enabled():
        return

    def create_event():
        GraphChangeEvent.objects.create(
            action=action,
            graph_label=graph_label or "",
            model_path=model_path or "",
            object_uuid=str(object_uuid) if object_uuid else "",
            payload=payload or {},
        )

    transaction.on_commit(create_event)


def process_graph_event(event, runner):
    action = event.action
    label = event.graph_label
    uuid = event.object_uuid
    link_key = event.payload.get("link_key")

    try:
        if action == GraphChangeEvent.ACTION_UPSERT_NODE:
            runner.project_node_by_label(label, uuid)
            runner.project_outgoing_links_by_label(label, uuid)
        elif action == GraphChangeEvent.ACTION_DELETE_NODE:
            runner.delete_node_by_label(label, uuid)
        elif action == GraphChangeEvent.ACTION_RESYNC_LINKS:
            runner.project_outgoing_links_by_label(label, uuid)
        elif action == GraphChangeEvent.ACTION_RESYNC_LINK_SOURCE:
            runner.project_link_source_by_key(link_key, uuid)
        elif action == GraphChangeEvent.ACTION_RESYNC_LINK_FULL:
            runner.project_link_by_key(link_key)
        elif action == GraphChangeEvent.ACTION_UPSERT_JUNCTION:
            runner.project_junction_by_key(link_key, uuid)
        elif action == GraphChangeEvent.ACTION_DELETE_JUNCTION:
            runner.delete_junction_relation(runner.get_link_def(link_key), uuid)
        else:
            raise ValueError(f"Unsupported graph event action: {action}")
    except ObjectDoesNotExist:
        # A row can be saved and deleted before the outbox drains. Treat the
        # missing source as a delete so replaying old events remains harmless.
        if action in {
            GraphChangeEvent.ACTION_UPSERT_NODE,
            GraphChangeEvent.ACTION_RESYNC_LINKS,
            GraphChangeEvent.ACTION_RESYNC_LINK_SOURCE,
        }:
            runner.delete_node_by_label(label, uuid)
        elif action in {
            GraphChangeEvent.ACTION_UPSERT_JUNCTION,
            GraphChangeEvent.ACTION_DELETE_JUNCTION,
        }:
            runner.delete_junction_relation(runner.get_link_def(link_key), uuid)


def mark_event_processing(event):
    event.status = GraphChangeEvent.STATUS_PROCESSING
    event.attempts += 1
    event.error = ""
    event.save(update_fields=["status", "attempts", "error", "updated_at"])


def mark_event_done(event):
    event.status = GraphChangeEvent.STATUS_DONE
    event.processed_at = timezone.now()
    event.error = ""
    event.save(update_fields=["status", "processed_at", "error", "updated_at"])



def mark_event_failed(event, exc):
    event.status = GraphChangeEvent.STATUS_FAILED
    event.error = str(exc)
    event.save(update_fields=["status", "error", "updated_at"])
