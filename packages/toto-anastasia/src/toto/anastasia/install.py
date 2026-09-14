"""Installing packages into a Capsule: a run that is watched, not waited on.

`jobs.run` is submit-wait-collect inside one request, and that is the right
shape for a compile. An install is minutes of pip output that somebody wants
to see scroll, so it is a ROW that whoever looks at it advances: `refresh`
pulls whatever the runner has printed since last time, derives a phase and a
count from it, and closes the run when the runner has exited. The API poll
calls it; so does the beat, so a run nobody watched still closes.

WHAT IS PROMISED AND WHAT IS NOT. A phase and a count, per sepulka: resolving,
downloading and installing are not comparable stages, and one percentage over
all three "would be a lie told smoothly". The count is of REQUESTED
distributions pip has reported, because that number is known before pip
starts; the number of dependencies it will fetch is not known until resolution
ends, and a bar over an unknown total is the same lie.

WHERE THE PACKAGES GO. `/files/site-packages` inside the runner — the
Capsule's files area (todo 9.7), which lives as long as the reservation. So an
install outlives its job and every unmount, `run_python` finds it on its
PYTHONPATH, and it dies with the Capsule. The runner image is never written.

REFUSED BEFORE IT STARTS on a Capsule reserved without internet access. pip
would fail at the first connection anyway — the posture is "a job that
unexpectedly has no network fails loudly" — but a refusal in a sentence beats
a log full of connection errors, and it leaves nothing behind.
"""

from __future__ import annotations

import logging
import re
import uuid as uuid_module

from django.db.models import F, TextField, Value
from django.db.models.functions import Concat
from django.utils import timezone

from . import choices, conf, execute, families, jobs
from .models import InstallRun
from .runtime import get_backend

log = logging.getLogger(__name__)

OPERATION = "install_packages"

#: What can be installed, and how each kind reaches its operation. Python
#: distributions go to /files/site-packages through pip; CTAN packages go to
#: /files/texmf through `anastasia-install-latex`. Both print pip's progress
#: words, so one phase and one count are derived the same way for either.
KINDS = {
    "python": {"operation": "install_packages", "param": "dists",
               "noun": "distributions"},
    "latex": {"operation": "install_latex_packages", "param": "packages",
              "noun": "packages"},
}

#: The refusal code for a Capsule without egress. Beside the ones in
#: services.py; here because only an install can earn it.
NO_EGRESS = "no_egress"
#: A LaTeX install on a platform with no CTAN mirror configured.
NO_MIRROR = "no_mirror"
#: A kind that is not in KINDS.
BAD_KIND = "bad_kind"

#: How much of the runner's output the row keeps, in characters. A log is a
#: diagnostic, not an archive: past this the copy stops growing and says so,
#: while the runner's own log stays readable through `execution_logs` for as
#: long as the runner exists.
MAX_LOG_CHARS = 512 * 1024

#: How many slices one refresh pulls before yielding. A refresh is called from
#: a request; it must not sit in a loop draining a chatty runner.
MAX_SLICES_PER_REFRESH = 8

#: Phases, in the order pip goes through them. Derived from the log, never
#: typed by a caller — see `_phase`.
PHASES = ("queued", "resolving", "downloading", "installing", "finished",
          "failed")

_SUCCESS_LINE = re.compile(r"^Successfully installed (.+)$", re.M)
_SATISFIED_LINE = re.compile(r"^Requirement already satisfied: ([A-Za-z0-9._-]+)",
                             re.M)


def _normalise(name: str) -> str:
    """PEP 503, the same rule `families.Param` applies to the request."""
    return re.sub(r"[-_.]+", "-", name.lower())


# --------------------------------------------------------------------------- #
# Starting                                                                     #
# --------------------------------------------------------------------------- #

def start(*, lease, dists: str, requested_by=None, timeout=None,
          kind: str = "python") -> InstallRun:
    """Submit the install job and open its run row, or refuse with a sentence.

    NOTHING IS LEFT BEHIND BY A REFUSAL: the row is created only after
    `execute.submit` has accepted the job, so a full Capsule or an exhausted
    quota produces the audit line submit already writes and no orphan run. The
    run's uuid is minted first so the execution can carry it as its subject.

    `dists` is the "+"-joined names for either kind; the name is historical.
    """
    spec = KINDS.get(kind)
    if spec is None:
        raise execute.CannotExecute(
            f"“{kind}” is not something that can be installed; the kinds are "
            f"{', '.join(sorted(KINDS))}.", BAD_KIND)
    if not lease.egress:
        raise execute.CannotExecute(
            f"“{lease.name}” was reserved without internet access, so nothing "
            "can be fetched into it. Reserve a Capsule with internet access to "
            "install packages.", NO_EGRESS)
    params = {spec["param"]: dists}
    if kind == "latex":
        mirror = conf.ctan_mirror()
        if not mirror:
            raise execute.CannotExecute(
                "This platform has no CTAN mirror configured, so LaTeX packages "
                "cannot be installed. An administrator sets "
                "ANASTASIA_CTAN_MIRROR.", NO_MIRROR)
        params["mirror"] = mirror
    op = families.operation(spec["operation"])
    try:
        clean = op.clean(params)
    except families.ParamError as exc:
        raise execute.CannotExecute(str(exc)) from exc
    names = clean[spec["param"]].split("+")

    run_uuid = uuid_module.uuid4()
    execution = execute.submit(
        lease=lease, operation=spec["operation"], params=clean,
        timeout=timeout, subject_label="anastasia.InstallRun",
        subject_id=str(run_uuid), requested_by=requested_by)
    return InstallRun.objects.create(
        uuid=run_uuid, lease=lease, execution=execution, kind=kind,
        requested_by=requested_by if getattr(requested_by, "pk", None) else None,
        packages=names, packages_total=len(names),
        status=choices.RUNNING, phase="resolving",
        started_at=timezone.now())


# --------------------------------------------------------------------------- #
# Advancing                                                                    #
# --------------------------------------------------------------------------- #

def refresh(run: InstallRun) -> InstallRun:
    """Pull what is new, derive phase and count, close if the job has ended.

    IDEMPOTENT AND NEVER RAISES. Called from a request and from the beat; a
    runtime that does not answer leaves the row as it was, with its cursor,
    and the next call tries again.
    """
    if run.is_finished:
        return run
    execution = run.execution
    if execution is None:
        _close(run, status=choices.LOST, phase="failed",
               detail="The compute manager lost track of this install.")
        return run

    backend = get_backend()
    _pull_log(run, backend, execution)
    if not execution.is_finished:
        status = _exit_state(backend, execution)
        if status is not None:
            # IT HAS EXITED. Pull once more BEFORE closing it, because closing
            # destroys the runner and its log with it — and the last lines are
            # exactly the ones that matter: "Successfully installed …" is what
            # `packages_done` is counted from. Reading only before the exit
            # check would undercount every install that finished between two
            # polls, which is most of them.
            _pull_log(run, backend, execution)
            _close_execution(backend, execution, status)
    if execution.is_finished:
        _close_from_execution(run, execution)
    else:
        _derive(run)
    return run


def cancel(run: InstallRun, *, reason: str = "", actor=None) -> InstallRun:
    """Stop the job, then let `refresh` close the run from its execution."""
    execution = run.execution
    if execution is not None and not execution.is_finished:
        execute.kill(execution, reason=reason or "This install was stopped.")
        execution.refresh_from_db()
    return refresh(run)


def sweep() -> int:
    """Advance every open run. The beat's share of the work; returns how many
    it closed, so the tick's report has a number."""
    closed = 0
    open_runs = (InstallRun.objects
                 .filter(status__in=(choices.PENDING, choices.RUNNING))
                 .select_related("execution", "lease"))
    for run in open_runs:
        try:
            refresh(run)
        except Exception:  # noqa: BLE001 — one stuck run must not stop the sweep
            log.exception("anastasia: could not refresh install %s", run.uuid)
            continue
        if run.is_finished:
            closed += 1
    return closed


def describe(run: InstallRun) -> dict:
    """The run as a client sees it. The log is separate and on request."""
    return {
        "uuid": str(run.uuid),
        "kind": run.kind,
        "capsule": str(run.lease.uuid),
        "execution": str(run.execution.uuid) if run.execution_id else None,
        "status": run.status,
        "finished": run.is_finished,
        "phase": run.phase,
        "packages": list(run.packages),
        "packages_done": run.packages_done,
        "packages_total": run.packages_total,
        "detail": run.detail,
        "log_length": len(run.log),
        "log_truncated": run.log_truncated,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


# --------------------------------------------------------------------------- #
# The pieces                                                                   #
# --------------------------------------------------------------------------- #

def _pull_log(run: InstallRun, backend, execution) -> None:
    """Append what the runner printed since the cursor. In SQL, deliberately.

    `Concat(F("log"), Value(text))` rather than `run.log += text; run.save()`:
    the row has more than one writer — this poll, the beat, a cancel — and a
    `save()` here would write back every field, including ones another
    writer changed since this instance was read. The same reason sepulka's
    builder updates by pk.
    """
    reader = getattr(backend, "execution_logs", None)
    if reader is None:
        return
    for _ in range(MAX_SLICES_PER_REFRESH):
        try:
            slice_ = reader(execution, run.log_offset) or {}
        except Exception:  # noqa: BLE001 — a poll must not 500 on a silent runtime
            log.warning("anastasia: could not read the install log for %s",
                        run.uuid, exc_info=True)
            return
        text = slice_.get("text") or ""
        try:
            new_offset = max(int(slice_.get("offset", run.log_offset) or 0),
                             run.log_offset)
        except (TypeError, ValueError):
            new_offset = run.log_offset
        if not text and new_offset == run.log_offset:
            return
        fields = {"log_offset": new_offset}
        if text and not run.log_truncated:
            room = MAX_LOG_CHARS - len(run.log)
            if len(text) > room:
                # Keep what fits and say the copy stopped here. The cursor
                # still advances so the run can close when the runner exits.
                text = text[:max(room, 0)]
                fields["log_truncated"] = True
            if text:
                fields["log"] = Concat(F("log"), Value(text),
                                       output_field=TextField())
        InstallRun.objects.filter(pk=run.pk).update(**fields)
        run.refresh_from_db(fields=["log", "log_offset", "log_truncated"])
        if slice_.get("complete", True) or not slice_.get("found", True):
            return


def _exit_state(backend, execution):
    """The runner's settled state, or None while it is still going.

    SPLIT FROM THE CLOSING so the caller can read the tail of the log in
    between — see `refresh`. This half only asks; it changes nothing.
    """
    status_of = getattr(backend, "execution_status", None)
    if status_of is None:
        return None
    try:
        status = status_of(execution) or {}
    except Exception:  # noqa: BLE001 — a poll must never 500 on a silent runtime
        log.warning("anastasia: could not read the status of install job %s",
                    execution.uuid, exc_info=True)
        return None
    if status.get("found") and status.get("running"):
        started = execution.started_at or execution.created_at
        patience = execution.timeout_seconds * jobs.PATIENCE_MULTIPLIER
        if (timezone.now() - started).total_seconds() <= patience:
            return None
        # Past the deadline AND past the patience `jobs.run` would have had.
        # The executor kills at the deadline on its own; if it has not, this
        # runner is not something the row can wait on any longer.
        return {"found": True, "running": True, "abandoned": True}
    return status


def _close_execution(backend, execution, status: dict) -> None:
    """Close the execution the way `jobs.run` closes one.

    THE SAME THREE STEPS in the same order — `_collect`, `_finish`, `_cleanup`
    — so a finished install gets the identical sentence a finished compile
    would, including the OOM and the cannot-see-into-the-guest cases. Reusing
    them rather than restating them is the point: a fourth way to close an
    execution would drift from the other three.
    """
    _outputs, report = jobs._collect(backend, execution)
    jobs._finish(execution, status, report)
    jobs._cleanup(backend, execution)
    execution.refresh_from_db()


def _reported(text: str, kind: str = "python") -> set:
    """The packages the installer has reported as present.

    PYTHON: "Successfully installed numpy-2.1.0 pandas-2.2.3" names each with
    its version after the LAST dash before a digit; "Requirement already
    satisfied: numpy" names one already there. Both count: the request was
    for the name to be importable, and it is.

    LATEX: "Successfully installed pst-3dplot" names the CTAN id as it is.
    The version-stripping rule above would turn that into "pst" — CTAN ids
    legitimately end in a dash and a digit — so it is Python's alone.
    """
    names = set()
    for match in _SUCCESS_LINE.finditer(text):
        for token in match.group(1).split():
            if kind == "python":
                names.add(_normalise(re.sub(r"-\d\S*$", "", token)))
            else:
                names.add(token.lower())
    if kind == "python":
        for match in _SATISFIED_LINE.finditer(text):
            names.add(_normalise(match.group(1)))
    return names


def _phase(text: str) -> str:
    """The furthest stage the log shows the installer has reached."""
    if "Installing collected packages" in text:
        return "installing"
    if "Downloading" in text:
        return "downloading"
    if "Collecting" in text or "Requirement already satisfied" in text:
        return "resolving"
    return "queued"


def _derive(run: InstallRun) -> None:
    """Phase and count from the log copy. Written only when they moved.

    THE PHASE NEVER MOVES BACKWARDS. `start` writes "resolving"; a first
    refresh before the installer has printed anything used to recompute
    "queued" from the empty log and show the run going back a step.
    """
    done = len(set(run.packages) & _reported(run.log, run.kind))
    phase = _phase(run.log)
    if (phase in PHASES and run.phase in PHASES
            and PHASES.index(phase) < PHASES.index(run.phase)):
        phase = run.phase
    fields = {}
    if done != run.packages_done:
        fields["packages_done"] = done
    if phase != run.phase:
        fields["phase"] = phase
    if fields:
        InstallRun.objects.filter(pk=run.pk).update(**fields)
        for name, value in fields.items():
            setattr(run, name, value)


def _close_from_execution(run: InstallRun, execution) -> None:
    done = len(set(run.packages) & _reported(run.log, run.kind))
    noun = KINDS.get(run.kind, KINDS["python"])["noun"]
    tool = "pip" if run.kind == "python" else "The installer"
    if execution.status == choices.SUCCESS:
        if run.packages_total and done < run.packages_total:
            # It exited 0 but did not name every requested package. Possible
            # — a name that resolved to nothing new prints differently across
            # pip versions — and worth a sentence rather than a green tick
            # over a count that does not add up.
            detail = (f"{tool} reported success but named only {done} of "
                      f"{run.packages_total} requested {noun}; read the log.")
        else:
            detail = (f"Installed {done} of {run.packages_total} requested "
                      f"{noun} into this Capsule.")
        _close(run, status=choices.SUCCESS, phase="finished", detail=detail,
               done=done, finished_at=execution.finished_at)
    else:
        _close(run, status=execution.status, phase="failed",
               detail=execution.error or "This install did not finish.",
               done=done, finished_at=execution.finished_at)


def _close(run: InstallRun, *, status: str, phase: str, detail: str,
           done: int | None = None, finished_at=None) -> None:
    fields = {
        "status": status,
        "phase": phase,
        "detail": (detail or "")[:500],
        "finished_at": finished_at or timezone.now(),
    }
    if done is not None:
        fields["packages_done"] = done
    InstallRun.objects.filter(pk=run.pk).update(**fields)
    for name, value in fields.items():
        setattr(run, name, value)
