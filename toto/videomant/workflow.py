"""
Workflow integration for videomant.

Resolves path expressions in node config values and builds MediaJob
instances from WorkflowNodeRun instances.
"""

from __future__ import annotations

CELERY_TASK_REGISTRY: dict[str, str] = {
    "videomant.probe":       "toto.videomant.tasks.probe",
    "videomant.compress":    "toto.videomant.tasks.compress",
    "videomant.resize":      "toto.videomant.tasks.resize",
    "videomant.cut":         "toto.videomant.tasks.cut",
    "videomant.extract_mp3": "toto.videomant.tasks.extract_mp3",
    "videomant.thumbnail":   "toto.videomant.tasks.thumbnail",
    "videomant.gif":         "toto.videomant.tasks.gif",
    "videomant.concat":      "toto.videomant.tasks.concat",
}


def _resolve_vault_file(ref):
    """Resolve {"kind": "vault_file", "id": N} → VaultFile or raise."""
    from toto.vault.models import VaultFile
    if isinstance(ref, dict) and ref.get("kind") == "vault_file":
        return VaultFile.objects.get(pk=ref["id"])
    raise ValueError(f"Expected vault_file ref, got: {ref!r}")


def resolve_value(value: str, workflow_run, node_run):
    """
    Resolve a path expression against the workflow context.

    Supported patterns:
      $.workflow.input.<key>
      $.nodes.<node_key>.outputs.main
      $.previous.outputs.main
    """
    if not isinstance(value, str) or not value.startswith("$"):
        return value

    parts = value.lstrip("$").lstrip(".").split(".")

    if not parts:
        return value

    if parts[0] == "workflow" and len(parts) >= 3 and parts[1] == "input":
        input_key = parts[2]
        raw = (workflow_run.input_data or {}).get(input_key)
        if isinstance(raw, dict) and raw.get("kind") == "vault_file":
            return _resolve_vault_file(raw)
        return raw

    if parts[0] == "nodes" and len(parts) >= 4:
        node_key = parts[1]
        # parts[2] == "outputs", parts[3] == output name (e.g. "main")
        output_key = parts[3]
        from toto.workflows.models import WorkflowNodeRun
        target_run = (
            WorkflowNodeRun.objects
            .filter(workflow_run=workflow_run, node__label=node_key)
            .first()
        )
        if target_run is None:
            raise ValueError(f"No node with label {node_key!r} found in workflow run #{workflow_run.id}")
        data = (target_run.output_data or {}).get("data", {})
        outputs = data.get("outputs", {})
        raw = outputs.get(output_key)
        if isinstance(raw, dict) and raw.get("kind") == "vault_file":
            return _resolve_vault_file(raw)
        return raw

    if parts[0] == "previous" and len(parts) >= 3 and parts[1] == "outputs":
        output_key = parts[2]
        from toto.workflows.models import WorkflowNodeRun, WorkflowEdgeRun
        # Find the immediately preceding completed node run
        activated_sources = (
            WorkflowEdgeRun.objects
            .filter(workflow_run=workflow_run, edge__target=node_run.node, activated=True)
            .select_related("edge__source")
        )
        for er in activated_sources:
            source_run = (
                WorkflowNodeRun.objects
                .filter(workflow_run=workflow_run, node=er.edge.source)
                .first()
            )
            if source_run:
                data = (source_run.output_data or {}).get("data", {})
                outputs = data.get("outputs", {})
                raw = outputs.get(output_key)
                if isinstance(raw, dict) and raw.get("kind") == "vault_file":
                    return _resolve_vault_file(raw)
                return raw

    raise ValueError(f"Cannot resolve workflow path: {value!r}")


def build_job_from_node_run(node_run) -> "MediaJob":
    """Create and save a MediaJob from a WorkflowNodeRun."""
    from toto.vault.models import VaultFile
    from .models import MediaJob

    node = node_run.node
    workflow_run = node_run.workflow_run
    config = node.config or {}
    task_name = node.task_name

    def _r(val):
        return resolve_value(val, workflow_run, node_run)

    params = {k: v for k, v in config.items() if k not in ("source", "sources", "secondary", "owner_id")}

    input_file = None
    source = config.get("source")
    if source:
        resolved = _r(source)
        if isinstance(resolved, VaultFile):
            input_file = resolved

    secondary_file = None
    secondary = config.get("secondary")
    if secondary:
        resolved = _r(secondary)
        if isinstance(resolved, VaultFile):
            secondary_file = resolved

    input_files = []
    sources = config.get("sources", [])
    for s in sources:
        resolved = _r(s)
        if isinstance(resolved, VaultFile):
            input_files.append(resolved.id)
        elif isinstance(resolved, int):
            input_files.append(resolved)

    owner = None
    owner_id = config.get("owner_id")
    if owner_id:
        from django.contrib.auth.models import User
        try:
            owner = User.objects.get(pk=owner_id)
        except User.DoesNotExist:
            pass

    job = MediaJob.objects.create(
        task_name=task_name,
        owner=owner,
        workflow_run=workflow_run,
        workflow_node_run=node_run,
        input_file=input_file,
        secondary_file=secondary_file,
        input_files=input_files,
        params=params,
    )
    return job


def make_output_envelope(job) -> dict:
    """Build the standard videomant task output dict for workflow completion."""
    main_ref = None
    if job.output_file_id:
        vf = job.output_file
        main_ref = {
            "kind": "vault_file",
            "id": vf.id,
            "filename": os.path.basename(vf.file.name) if vf.file else "",
            "content_type": _content_type_for(vf),
        }

    envelope = {
        "ok": True,
        "kind": "media",
        "task": job.task_name,
        "media_job_id": job.id,
        "outputs": {"main": main_ref} if main_ref else {},
        "metadata": job.output_metadata or {},
        "progress_percent": job.progress_percent,
    }
    return {"data": envelope}


def _content_type_for(vf) -> str:
    _map = {
        "video": "video/mp4",
        "audio": "audio/mpeg",
        "image": "image/jpeg",
    }
    return _map.get(vf.file_type, "application/octet-stream")


import os  # noqa: E402 — placed after function defs to avoid circular at module load
