SYNC_REGISTRY = {}


def register_sync_adapter(adapter_class):
    adapter = adapter_class()

    if not adapter.model:
        raise ValueError(f"{adapter_class.__name__} is missing model.")

    model_label = adapter.model_label or adapter.model._meta.label
    adapter.model_label = model_label

    SYNC_REGISTRY[model_label] = adapter
    return adapter_class


def get_sync_adapter(model_label):
    try:
        return SYNC_REGISTRY[model_label]
    except KeyError:
        raise LookupError(f"No sync adapter registered for {model_label}.")


def get_registered_model_labels():
    return sorted(SYNC_REGISTRY.keys())
