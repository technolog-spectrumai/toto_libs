from django.core.exceptions import FieldDoesNotExist
from django.db.models.signals import m2m_changed, post_delete, post_save

from .loader import import_model, load_all_configs
from .models import GraphChangeEvent
from .projection import link_key
from .sync import enqueue_graph_event


_registered = False


def _model_path(model):
    return f"{model.__module__}.{model.__name__}"


def _object_uuid(obj):
    if hasattr(obj, "uid"):
        return str(obj.uid)
    return str(obj.pk)


def _connect_node_signals(label, model, uuid_field):
    model_path = _model_path(model)

    def handle_save(sender, instance, **kwargs):
        enqueue_graph_event(
            GraphChangeEvent.ACTION_UPSERT_NODE,
            graph_label=label,
            model_path=model_path,
            object_uuid=getattr(instance, uuid_field),
        )

    def handle_delete(sender, instance, **kwargs):
        enqueue_graph_event(
            GraphChangeEvent.ACTION_DELETE_NODE,
            graph_label=label,
            model_path=model_path,
            object_uuid=getattr(instance, uuid_field),
        )

    post_save.connect(
        handle_save,
        sender=model,
        weak=False,
        dispatch_uid=f"ravioli.node.save.{label}.{model_path}",
    )
    post_delete.connect(
        handle_delete,
        sender=model,
        weak=False,
        dispatch_uid=f"ravioli.node.delete.{label}.{model_path}",
    )


def _relation_field(model, field_name):
    try:
        return model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return None


def _m2m_through_model(field):
    remote_field = getattr(field, "remote_field", None)
    return (
        getattr(field, "through", None)
        or getattr(remote_field, "through", None)
    )


def _connect_m2m_link_signal(link_def, from_model, from_uuid_field):
    field = _relation_field(from_model, link_def["source"])
    if field is None or not getattr(field, "many_to_many", False):
        return

    through = _m2m_through_model(field)
    if through is None:
        return

    key = link_key(link_def)
    from_label = link_def["from_label"]

    def handle_m2m(sender, instance, action, pk_set=None, **kwargs):
        if action not in {"post_add", "post_remove", "post_clear"}:
            return

        if isinstance(instance, from_model):
            enqueue_graph_event(
                GraphChangeEvent.ACTION_RESYNC_LINK_SOURCE,
                graph_label=from_label,
                model_path=_model_path(from_model),
                object_uuid=getattr(instance, from_uuid_field),
                payload={"link_key": key},
            )
            return

        if pk_set:
            for obj in from_model.objects.filter(pk__in=pk_set).iterator():
                enqueue_graph_event(
                    GraphChangeEvent.ACTION_RESYNC_LINK_SOURCE,
                    graph_label=from_label,
                    model_path=_model_path(from_model),
                    object_uuid=getattr(obj, from_uuid_field),
                    payload={"link_key": key},
                )
            return

        # Reverse-side clear does not tell us which from-model rows changed,
        # so fall back to a full resync for this one link definition.
        enqueue_graph_event(
            GraphChangeEvent.ACTION_RESYNC_LINK_FULL,
            graph_label=from_label,
            model_path=_model_path(from_model),
            payload={"link_key": key},
        )

    m2m_changed.connect(
        handle_m2m,
        sender=through,
        weak=False,
        dispatch_uid=f"ravioli.link.m2m.{key}",
    )


def _connect_junction_signals(link_def):
    via_model = import_model(link_def["via_model"])
    key = link_key(link_def)
    model_path = _model_path(via_model)

    def handle_save(sender, instance, **kwargs):
        enqueue_graph_event(
            GraphChangeEvent.ACTION_UPSERT_JUNCTION,
            graph_label=link_def["from_label"],
            model_path=model_path,
            object_uuid=_object_uuid(instance),
            payload={"link_key": key},
        )

    def handle_delete(sender, instance, **kwargs):
        enqueue_graph_event(
            GraphChangeEvent.ACTION_DELETE_JUNCTION,
            graph_label=link_def["from_label"],
            model_path=model_path,
            object_uuid=_object_uuid(instance),
            payload={"link_key": key},
        )

    post_save.connect(
        handle_save,
        sender=via_model,
        weak=False,
        dispatch_uid=f"ravioli.junction.save.{key}",
    )
    post_delete.connect(
        handle_delete,
        sender=via_model,
        weak=False,
        dispatch_uid=f"ravioli.junction.delete.{key}",
    )


def register_graph_signals(configs=None):
    global _registered
    if _registered:
        return

    configs = configs if configs is not None else load_all_configs()
    label_map = {}

    for config in configs:
        for node_def in config.get("nodes", []):
            label = node_def["label"]
            model = import_model(node_def["model"])
            uuid_field = node_def.get("uuid_field", "uid")
            label_map[label] = {
                "model": model,
                "uuid_field": uuid_field,
            }
            _connect_node_signals(label, model, uuid_field)

    for config in configs:
        for link_def in config.get("links", []):
            if link_def.get("via_model"):
                _connect_junction_signals(link_def)
                continue

            from_info = label_map.get(link_def["from_label"])
            if from_info:
                _connect_m2m_link_signal(
                    link_def,
                    from_info["model"],
                    from_info["uuid_field"],
                )

    _registered = True
