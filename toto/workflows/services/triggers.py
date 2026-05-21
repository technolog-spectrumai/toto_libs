from __future__ import annotations

import mimetypes
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import Workflow, WorkflowNode, WorkflowRun, WorkflowRunFile, WorkflowTriggerInput


class TriggerValidationError(Exception):
    def __init__(self, errors: dict[str, list[str]]):
        self.errors = errors
        super().__init__("; ".join(f"{key}: {', '.join(value)}" for key, value in errors.items()))


@dataclass
class PreparedTriggerSubmission:
    trigger_node: WorkflowNode
    parameters: dict[str, Any]
    file_uploads: dict[str, list]
    existing_files: dict[str, list[dict]]
    inputs: dict[str, Any]


def get_trigger_node(workflow: Workflow) -> WorkflowNode | None:
    return (
        workflow.nodes.filter(node_type=WorkflowNode.TRIGGER)
        .prefetch_related("trigger_inputs")
        .order_by("id")
        .first()
    )


def trigger_inputs(trigger_node: WorkflowNode):
    return trigger_node.trigger_inputs.order_by("order", "id")


def prepare_trigger_submission(workflow: Workflow, post_data, file_data) -> PreparedTriggerSubmission:
    trigger_node = get_trigger_node(workflow)
    if trigger_node is None:
        raise TriggerValidationError({"__all__": ["Workflow has no Trigger Node."]})

    errors: dict[str, list[str]] = {}
    parameters: dict[str, Any] = {}
    file_uploads: dict[str, list] = {}
    existing_files: dict[str, list[dict]] = {}

    for item in trigger_inputs(trigger_node):
        if item.input_type == WorkflowTriggerInput.TYPE_FILE:
            files = list(file_data.getlist(item.key)) if hasattr(file_data, "getlist") else []
            existing = [
                value for value in post_data.getlist(f"{item.key}__existing")
                if str(value).strip()
            ] if hasattr(post_data, "getlist") else []
            existing_refs = _decode_existing_file_refs(item, existing, errors)
            _validate_file_input(item, files, existing, errors)
            file_uploads[item.key] = files
            existing_files[item.key] = existing_refs
            continue

        if item.input_type == WorkflowTriggerInput.TYPE_DATETIME:
            value = _clean_datetime(item, post_data, errors)
        else:
            value = _clean_scalar(item, post_data.get(item.key, ""), errors)
        if value is not None or item.default_value is not None:
            parameters[item.key] = value if value is not None else item.default_value

    if errors:
        raise TriggerValidationError(errors)

    return PreparedTriggerSubmission(
        trigger_node=trigger_node,
        parameters=parameters,
        file_uploads=file_uploads,
        existing_files=existing_files,
        inputs=dict(parameters),
    )


def create_triggered_run(
    *,
    workflow: Workflow,
    submission: PreparedTriggerSubmission,
) -> WorkflowRun:
    run = WorkflowRun.objects.create(workflow=workflow, input_data={})
    files_payload = _save_uploaded_files(run, submission)
    inputs = {**submission.parameters, **files_payload}
    lambda_payload = {
        "workflow_id": str(workflow.id),
        "run_id": str(run.id),
        "trigger_node_id": str(submission.trigger_node.id),
        "inputs": inputs,
    }
    run.input_data = {
        "trigger_node_id": submission.trigger_node.id,
        "parameters": submission.parameters,
        "files": files_payload,
        "inputs": inputs,
        "lambda_payload": lambda_payload,
    }
    run.save(update_fields=["input_data"])
    return run


def trigger_payload_for_run(run: WorkflowRun, trigger_node: WorkflowNode | None = None) -> dict:
    data = run.input_data or {}
    payload = data.get("lambda_payload")
    if isinstance(payload, dict):
        return payload
    inputs = data.get("inputs") or {
        **(data.get("parameters") or {}),
        **(data.get("files") or {}),
    }
    return {
        "workflow_id": str(run.workflow_id),
        "run_id": str(run.id),
        "trigger_node_id": str(
            trigger_node.id if trigger_node is not None else data.get("trigger_node_id", "")
        ),
        "inputs": inputs,
    }


def rerun_initial_values(run: WorkflowRun) -> dict:
    data = run.input_data or {}
    return {
        "parameters": data.get("parameters") or {},
        "files": data.get("files") or {},
    }


def _clean_scalar(item: WorkflowTriggerInput, raw: str, errors: dict[str, list[str]]):
    raw = str(raw or "").strip()
    if not raw:
        if item.required and item.default_value is None:
            _add_error(errors, item.key, "This field is required.")
        return None
    try:
        if item.input_type == WorkflowTriggerInput.TYPE_INT:
            return int(raw)
        if item.input_type == WorkflowTriggerInput.TYPE_FLOAT:
            return float(raw)
    except ValueError:
        _add_error(errors, item.key, f"Enter a valid {item.get_input_type_display().lower()}.")
        return None
    return raw


def _clean_datetime(item: WorkflowTriggerInput, post_data, errors: dict[str, list[str]]):
    date_raw = str(post_data.get(f"{item.key}__date", "") or "").strip()
    time_raw = str(post_data.get(f"{item.key}__time", "") or "").strip()
    if not date_raw and not time_raw:
        if item.required and item.default_value is None:
            _add_error(errors, item.key, "Date and time are required.")
        return None
    if not date_raw or not time_raw:
        _add_error(errors, item.key, "Date and time must both be provided.")
        return None
    try:
        naive = datetime.fromisoformat(f"{date_raw}T{time_raw}")
    except ValueError:
        _add_error(errors, item.key, "Enter a valid date and time.")
        return None
    aware = timezone.make_aware(naive, timezone.get_current_timezone()) if timezone.is_naive(naive) else naive
    return aware.isoformat()


def _validate_file_input(item: WorkflowTriggerInput, files: list, existing: list, errors: dict[str, list[str]]) -> None:
    total_count = len(files) + len(existing)
    if item.required and total_count == 0:
        _add_error(errors, item.key, "At least one file is required.")
    if not item.allow_multiple_files and total_count > 1:
        _add_error(errors, item.key, "Only one file is allowed.")
    if item.max_file_count and total_count > item.max_file_count:
        _add_error(errors, item.key, f"Maximum file count is {item.max_file_count}.")

    accepted = _accepted_tokens(item)
    for uploaded in files:
        if item.max_file_size and uploaded.size > item.max_file_size:
            _add_error(errors, item.key, f"{uploaded.name} is larger than {item.max_file_size} bytes.")
        if accepted and not _file_matches(uploaded, accepted):
            _add_error(errors, item.key, f"{uploaded.name} is not an accepted file type.")


def _save_uploaded_files(run: WorkflowRun, submission: PreparedTriggerSubmission) -> dict[str, list[dict]]:
    payload: dict[str, list[dict]] = {}
    inputs_by_key = {item.key: item for item in trigger_inputs(submission.trigger_node)}
    file_keys = set(submission.file_uploads) | set(submission.existing_files)
    for key in file_keys:
        files = submission.file_uploads.get(key, [])
        refs = list(submission.existing_files.get(key, []))
        trigger_input = inputs_by_key.get(key)
        for uploaded in files:
            mime_type = uploaded.content_type or mimetypes.guess_type(uploaded.name)[0] or ""
            row = WorkflowRunFile.objects.create(
                workflow_run=run,
                trigger_input=trigger_input,
                input_key=key,
                file=uploaded,
                original_name=uploaded.name,
                mime_type=mime_type,
                size=uploaded.size,
                metadata={
                    "content_type": mime_type,
                    "upload_status": "stored",
                },
            )
            refs.append(row.as_reference())
        if refs:
            payload[key] = refs
    return payload


def _decode_existing_file_refs(item: WorkflowTriggerInput, values: list[str], errors: dict[str, list[str]]) -> list[dict]:
    refs = []
    for value in values:
        try:
            ref = json.loads(value)
        except (TypeError, ValueError):
            _add_error(errors, item.key, "Existing file reference is invalid.")
            continue
        if not isinstance(ref, dict) or not ref.get("file_id"):
            _add_error(errors, item.key, "Existing file reference is invalid.")
            continue
        refs.append(ref)
    return refs


def _accepted_tokens(item: WorkflowTriggerInput) -> list[str]:
    return [
        token.strip().lower()
        for token in (item.accepted_file_types or "").split(",")
        if token.strip()
    ]


def _file_matches(uploaded, accepted: list[str]) -> bool:
    name = str(uploaded.name or "").lower()
    mime_type = str(uploaded.content_type or mimetypes.guess_type(name)[0] or "").lower()
    for token in accepted:
        if token.startswith(".") and name.endswith(token):
            return True
        if token.endswith("/*") and mime_type.startswith(token[:-1]):
            return True
        if token == mime_type:
            return True
    return False


def _add_error(errors: dict[str, list[str]], key: str, message: str) -> None:
    errors.setdefault(key, []).append(message)
