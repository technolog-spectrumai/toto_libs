"""Run one job in a user's Gear and wait for its files. The caller's side.

Every migrating caller — aralia, texlab, ocr, the media services — needs the
same six steps: pick the Gear, tar the inputs, submit, poll, collect, close.
Written once here so there is one polling idiom on the platform rather than
four, and so a caller's own module stays about ITS domain.

**This blocks.** It is meant to be called from a celery worker, not a web
request: the worker holds the job while it runs, which is exactly what the
worker is for and exactly what a web process must never do. Callers already
refuse to run inline for that reason (``aralia.dispatch`` documents it), so
this changes nothing about where work happens — only where it *runs*.

The execution row is closed on every path, including the ones that raise. A
row left RUNNING is the one outcome a polling browser cannot recover from.
"""

from __future__ import annotations

import io
import logging
import tarfile
import time

from django.core.exceptions import ValidationError

from . import choices, execute, services
from .models import ComputeLease
from .runtime import RuntimeUnavailable, get_backend

log = logging.getLogger("toto.anastasia.jobs")

#: How often to ask. A second is short enough that a fast job is not padded and
#: long enough that a slow one does not hammer the manager: a 90-second LaTeX
#: compile costs 90 requests, which is nothing, and a 200 ms PDF still returns
#: in about a second.
POLL_SECONDS = 1.0

#: How long to keep polling before giving up on the manager itself. This is NOT
#: the job's timeout — the manager enforces that from the deadline label, and
#: kills the runner. This is the caller's patience with a manager that has
#: stopped answering, and it is deliberately longer so the two cannot race.
PATIENCE_MULTIPLIER = 2


class JobFailed(Exception):
    """The job ran and did not succeed. ``report`` says what the runner said."""

    def __init__(self, message, *, report=None, outputs=None, execution=None):
        super().__init__(message)
        self.report = report or {}
        self.outputs = outputs or {}
        self.execution = execution


class NoGear(ValidationError):
    """The caller has no mounted Gear to run in. The message names the page."""


def tar_of(files: dict) -> bytes:
    """``{name: bytes}`` to a tar the manager will accept.

    Names are flat and caller-chosen; the manager re-validates every one on the
    way in, so nothing here has to be the only check.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, body in files.items():
            data = body.encode("utf-8") if isinstance(body, str) else body
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def files_from(blob: bytes) -> dict:
    """The returned tar, as ``{name: bytes}``."""
    if not blob:
        return {}
    out = {}
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        for member in archive:
            if not member.isfile():
                continue
            handle = archive.extractfile(member)
            if handle is not None:
                out[member.name] = handle.read()
    return out


def usable_gears(user):
    """This user's Gears that could take work right now, newest first."""
    ready = []
    for lease in ComputeLease.objects.open().filter(owner=user):
        if services.derive_state(services.runtime_for(lease)) in choices.ACCEPTING:
            ready.append(lease)
    return ready


def require_gear(user, uuid=None) -> ComputeLease:
    """The Gear this job will run in, or a refusal naming the desk.

    There is NO auto-mount and no fallback to somebody else's capacity: the
    whole model is that a person decides to hold compute open. A caller that
    silently found a Gear would make that decision invisible.
    """
    if uuid:
        lease = ComputeLease.objects.open().filter(
            owner=user, uuid=uuid).first()
        if lease is None:
            raise NoGear(
                "That Compute Gear is not available any more. Pick another on "
                "the Compute Gears page.")
        state = services.derive_state(services.runtime_for(lease))
        if state not in choices.ACCEPTING:
            raise NoGear(
                f"“{lease.name}” is not ready to take work ({state}). Mount it "
                "on the Compute Gears page and try again.")
        return lease

    candidates = usable_gears(user)
    if not candidates:
        raise NoGear(
            "You have no mounted Compute Gear, so there is nowhere to run "
            "this. Reserve one and mount it on the Compute Gears page.")
    if len(candidates) > 1:
        # Ambiguity is the user's to resolve. Picking for them would make a
        # job land somewhere they did not choose, which is the opposite of
        # what a reservation is for.
        raise NoGear(
            "You have more than one mounted Gear, so this job needs you to say "
            "which one to use.")
    return candidates[0]


def run(*, lease: ComputeLease, operation: str, params: dict | None = None,
        inputs: dict | None = None, limits=None, timeout: int | None = None,
        subject_label: str = "", subject_id="", requested_by=None,
        poll_seconds: float = POLL_SECONDS) -> dict:
    """Submit, wait, collect. Returns ``{outputs, report, execution}``.

    Raises :class:`JobFailed` when the runner reported failure, was OOM-killed
    or was stopped at its deadline — with whatever it did produce attached, so
    a caller can still file a log from a compile that failed.
    """
    execution = execute.submit(
        lease=lease, operation=operation, params=params,
        payload=tar_of(inputs or {}), limits=limits, timeout=timeout,
        subject_label=subject_label, subject_id=subject_id,
        requested_by=requested_by)

    backend = get_backend()
    patience = execution.timeout_seconds * PATIENCE_MULTIPLIER
    deadline = time.monotonic() + patience

    try:
        status = _wait(backend, execution, deadline, poll_seconds)
    except Exception:
        execute.fail(execution, "The compute manager stopped answering while "
                                "this job was running.")
        _cleanup(backend, execution)
        raise

    outputs, report = _collect(backend, execution)
    _finish(execution, status, report)
    _cleanup(backend, execution)

    if execution.status != choices.SUCCESS:
        raise JobFailed(execution.error, report=report, outputs=outputs,
                        execution=execution)
    return {"outputs": outputs, "report": report, "execution": execution}


def start_runtime(*, lease: ComputeLease, operation: str,
                  params: dict | None = None, inputs: dict | None = None,
                  limits=None, timeout: int | None = None,
                  expect: str = "connection.json",
                  ready_seconds: float = 60.0,
                  subject_label: str = "", subject_id="",
                  requested_by=None, poll_seconds: float = 0.5) -> dict:
    """Start something that KEEPS RUNNING, and wait only for it to be ready.

    The counterpart to :func:`run` for the one family that is not a job. A
    LaTeX compile ends and hands back a PDF; a Python kernel comes up and then
    waits for somebody to talk to it, so waiting for exit would mean waiting
    for the user to finish their afternoon.

    Ready is defined by a FILE APPEARING in the output area — the runner writes
    its connection details there and then blocks. That works because ``/out``
    is a bind mount rather than a copy taken at exit: the manager can read it
    while the container is still alive, which is exactly the property a
    long-lived runtime needs and a batch job never exercises.

    Returns ``{execution, ready}`` where ``ready`` is the parsed file. The
    execution stays RUNNING and is the caller's to stop.
    """
    import json

    execution = execute.submit(
        lease=lease, operation=operation, params=params,
        payload=tar_of(inputs or {}), limits=limits, timeout=timeout,
        subject_label=subject_label, subject_id=subject_id,
        requested_by=requested_by)

    backend = get_backend()
    deadline = time.monotonic() + ready_seconds
    while True:
        status = backend.execution_status(execution)
        if not status.get("found"):
            execute.fail(execution,
                         "The compute manager lost the runtime while it was "
                         "starting.", status=choices.LOST)
            raise JobFailed(execution.error, execution=execution)

        # Look for the file BEFORE reacting to a dead container: a runtime that
        # wrote its details and then exited still handed us something usable,
        # and the caller should hear about the exit rather than about a
        # missing file.
        outputs, report = _collect(backend, execution)
        if expect in outputs:
            try:
                ready = json.loads(outputs[expect].decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                _stop(backend, execution,
                      f"The runtime wrote unreadable {expect} ({exc}).")
                raise JobFailed(execution.error, report=report,
                                execution=execution) from None
            return {"execution": execution, "ready": ready, "report": report}

        if not status.get("running"):
            _finish(execution, status, report)
            _cleanup(backend, execution)
            raise JobFailed(
                execution.error or "The runtime stopped before it was ready.",
                report=report, execution=execution)

        if time.monotonic() > deadline:
            _stop(backend, execution,
                  f"The runtime did not become ready within {ready_seconds:.0f}s.")
            raise JobFailed(execution.error, report=report,
                            execution=execution)
        time.sleep(poll_seconds)


def stop_runtime(execution, *, reason: str = "") -> None:
    """Destroy a runtime and its scratch. Idempotent.

    Killing a CONTAINER, note, rather than signalling a pid. The app this
    replaced had to guard every kill with a cmdline check because "from a
    container that did not start the kernel, session.pid may name an innocent
    process" — its own words. A container id names one thing on the machine
    and cannot come to mean another.
    """
    backend = get_backend()
    _stop(backend, execution, reason or "The runtime was stopped.")
    _cleanup(backend, execution)


def _stop(backend, execution, message: str) -> None:
    try:
        backend.kill_execution(execution)
    except Exception:  # noqa: BLE001 - the row closes either way
        log.warning("anastasia: could not kill %s", execution.uuid,
                    exc_info=True)
    execute.fail(execution, message, status=choices.KILLED)


def _wait(backend, execution, deadline, poll_seconds) -> dict:
    while True:
        status = backend.execution_status(execution)
        if not status.get("found"):
            # The manager has no record of it. Either it never started or
            # something reaped it; either way nothing is coming.
            return {"found": False}
        if not status.get("running"):
            return status
        if time.monotonic() > deadline:
            log.warning("anastasia: giving up polling %s", execution.uuid)
            return {"found": True, "running": True, "abandoned": True}
        time.sleep(poll_seconds)


def _collect(backend, execution) -> tuple:
    """The output files and the runner's own report. Never raises.

    A job that failed still produced something worth having — a LaTeX log, a
    traceback — and losing it because collection was strict would throw away
    the only diagnosis.
    """
    import json

    try:
        outputs = files_from(backend.collect(execution))
    except (RuntimeUnavailable, Exception):  # noqa: BLE001
        log.warning("anastasia: could not collect output for %s",
                    execution.uuid, exc_info=True)
        return {}, {}

    report = {}
    raw = outputs.pop("report.json", None)
    if raw:
        try:
            report = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("anastasia: unreadable report.json from %s",
                        execution.uuid)
    return outputs, report


def _finish(execution, status: dict, report: dict) -> None:
    """Close the execution row with the most useful sentence available.

    Order matters: the runner's own error is the best explanation when it has
    one, the OOM flag is the next best, and the exit code is a last resort
    because 137 means both "killed for memory" and "killed at the deadline".
    """
    usage = {k: report.get(k) for k in ("seconds", "bytes", "pages", "passes")
             if report.get(k) is not None}

    if not status.get("found"):
        execute.fail(execution, "The compute manager lost track of this job.",
                     status=choices.LOST)
        return
    if status.get("abandoned"):
        execute.fail(execution,
                     "This job was still running when we stopped waiting for "
                     "it.", status=choices.LOST)
        return
    if status.get("oom_killed"):
        execute.fail(execution,
                     "This job ran out of memory inside its Gear. Give the "
                     "Gear more RAM, or ask for less work at once.")
        return
    # The runner's own verdict WINS over the exit code when it has one.
    #
    # Not a preference — the exit code is unreliable in both directions. A
    # container OOM-killed by the kernel can still report exit 0 (observed:
    # a shell pipeline masks the death of the process before the pipe), and a
    # runner that failed cleanly reports the reason in a sentence the exit code
    # could never carry. report.json is written by the program that actually
    # did the work, so it is the better witness.
    if report.get("status") == "error":
        execute.fail(execution,
                     str(report.get("error") or "")
                     or "This job failed inside its Compute Gear.")
        execution.usage = usage
        execution.save(update_fields=["usage"])
        return

    execute.finish(execution, exit_code=status.get("exit_code") or 0,
                   usage=usage)


def _cleanup(backend, execution) -> None:
    """Destroy the runner and its scratch. Best effort, always attempted.

    The manager reaps orphans on its own loop, so a failure here costs a little
    scratch for a little while rather than leaking it forever.
    """
    finish = getattr(backend, "finish_execution", None)
    if finish is None:
        return
    try:
        finish(execution)
    except Exception:  # noqa: BLE001
        log.warning("anastasia: could not clean up %s", execution.uuid,
                    exc_info=True)
