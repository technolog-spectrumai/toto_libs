"""Operation name + validated parameters -> the argv a runner is given.

This table lives on the TRUSTED side on purpose. ``families.py`` says what a
caller may *ask for*; this says what actually runs, and a caller never sees it.
Keeping them apart is what stops a parameter from growing into a command: every
value below arrives already validated by ``Operation.clean`` — an enum, a
bounded int, or a path matched against ``_SAFE_PATH`` — so nothing here has to
quote or escape, and nothing here may start doing so. If an operation ever
needs free text on a command line, it needs a different design, not a quoting
function.

Each runner image exposes the same contract:

    /in       staged input, read-only
    /scratch  writable tmpfs, hard-sized
    /out      where results go; whatever lands here comes back

Django-free.
"""

from __future__ import annotations

from ..families import Operation


def build_argv(operation: Operation, params: dict) -> list[str]:
    """The command line for one execution."""
    builder = _BUILDERS.get(operation.name)
    if builder is None:                       # pragma: no cover - catalogue drift
        raise KeyError(
            f"operation {operation.name!r} has no runner argv; families.py and "
            "runners.py have drifted apart")
    return [str(part) for part in builder(params)]


def _render_pdf(params: dict) -> list:
    # The runner reads /in/input.html and writes /out/output.pdf. No parameters
    # at all: base_url is pinned to None inside the runner, so a document
    # cannot fetch anything — and the container has no network to fetch over.
    return ["anastasia-render-pdf"]


def _compile_latex(params: dict) -> list:
    return [
        "anastasia-compile-latex",
        "--main", params["main"],
        "--engine", params["engine"],
        "--max-passes", params["max_passes"],
        "--latexmk" if params["use_latexmk"] else "--no-latexmk",
    ]


def _run_media_command(params: dict) -> list:
    """Every declared parameter, as an explicit flag.

    Passed one flag at a time rather than as a JSON blob, and that is not
    style: a blob is an open channel, and the moment one exists somebody adds
    a key to it that reaches ffmpeg. Every value here has already been through
    ``Operation.clean`` — an enum, a bounded int, a matched time, or a path
    that cannot escape the staged input — so the runner receives nothing it
    has to re-parse.
    """
    argv = [
        "anastasia-run-media-command",
        "--command", params["command"],
        "--input", params["input"],
        "--output-name", params["output_name"],
        "--quality", params["quality"],
        "--bitrate", params["bitrate"],
        "--start-time", params["start_time"],
        "--duration", params["duration"],
        "--position", params["position"],
    ]
    if params.get("second"):
        argv += ["--second", params["second"]]
    if params.get("end_time"):
        argv += ["--end-time", params["end_time"]]
    for name in ("width", "height", "x", "y", "fps"):
        if params.get(name):
            argv += [f"--{name}", params[name]]
    if params.get("reencode"):
        argv.append("--reencode")
    return argv


def _run_ocr(params: dict) -> list:
    return [
        "anastasia-run-ocr",
        "--input", params["input"],
        "--lang", params["lang"],
        "--psm", params["psm"],
    ]


def _run_python(params: dict) -> list:
    # One script, once. The long-lived kernel runner this replaces wrote
    # /out/connection.json and stayed up until the Capsule was unmounted; there
    # is no such family any more, and nothing in a Capsule outlives its job.
    return ["anastasia-run-python", "--script", params["script"]]


_BUILDERS = {
    "render_pdf": _render_pdf,
    "compile_latex": _compile_latex,
    "run_media_command": _run_media_command,
    "run_ocr": _run_ocr,
    "run_python": _run_python,
}


def known_operations() -> frozenset:
    return frozenset(_BUILDERS)
