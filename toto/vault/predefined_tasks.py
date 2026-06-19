"""
Workflow predefined task for archiving vault files.

Only imported by toto.workflows' autodiscover (workflows/apps.py) for installed
apps, so the ``toto.workflows`` import below is safe — when workflows is absent
this module is never loaded.
"""
from toto.workflows.predefined_tasks import register


@register("vault_zip_files")
def vault_zip_files(input_data: dict) -> dict:
    """Zip the selected vault files into a new ``zip`` VaultFile.

    Expects input_data = {"data": {owner_id, source_directory_id,
    target_directory_id (nullable), file_ids, output_name}}.
    """
    from django.contrib.auth.models import User

    from toto.vault.archive import zip_files_to_vault_file
    from toto.vault.models import VaultDirectory

    data = input_data.get("data") or {}
    owner = User.objects.get(pk=data["owner_id"])
    source = VaultDirectory.objects.get(pk=data["source_directory_id"])
    target = (
        VaultDirectory.objects.get(pk=data["target_directory_id"])
        if data.get("target_directory_id")
        else None
    )
    vault_file, n_added = zip_files_to_vault_file(
        owner, source, target, data.get("file_ids") or [], data.get("output_name") or "",
    )
    return {"data": {"vault_file_id": vault_file.pk, "added": n_added}}
