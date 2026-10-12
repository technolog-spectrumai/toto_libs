"""System checks of the vault (2026-10-11): the object storages the operator
configured (``settings.VAULT_OBJECT_STORAGES``) are sound before a deploy is
promoted — a storage with a name used twice, no provider, no region or no
keys would be offered in the New bucket dialog and fail there."""
from django.core.checks import Error, register


@register()
def check_object_storages(app_configs, **kwargs):
    from .plugins.storage_adapters import configured_storage_faults

    return [Error(fault, hint="Fix the entry in the build's configuration (local.yaml and "
                              "storage_keys.yaml on Dracena).", id="vault.E010")
            for fault in configured_storage_faults()]
