from importlib import import_module

from django.apps import apps


def autodiscover_sync_adapters():
    """
    Import <app>.sync_adapters for every installed Django app when present.
    """
    for app_config in apps.get_app_configs():
        module_name = f"{app_config.name}.sync_adapters"
        try:
            import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
