from django.apps import apps
from django.utils.module_loading import import_module


def autodiscover_plugins(module_name: str):
    for app_config in apps.get_app_configs():
        full_module_name = f"{app_config.name}.{module_name}"

        try:
            import_module(full_module_name)
        except ModuleNotFoundError as exc:
            if exc.name and full_module_name.startswith(exc.name):
                continue
            raise
