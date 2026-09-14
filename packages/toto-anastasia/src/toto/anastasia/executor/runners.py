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
    # TEXMFHOME names the tree `install_latex_packages` writes, so a package
    # installed into this Capsule is found by every later compile. kpathsea
    # searches TEXMFHOME before the distribution and needs no ls-R there; a
    # tree that does not exist is simply not searched, so a Capsule that never
    # installed anything compiles exactly as before.
    return [
        "env", f"TEXMFHOME={TEXMF_HOME}",
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
    #
    # PYTHONPATH names the install target so packages an earlier
    # `install_packages` put in the Capsule's files area are importable. A
    # directory that does not exist is ignored by the interpreter, so a
    # Capsule that never installed anything runs exactly as before.
    return ["env", f"PYTHONPATH={SITE_PACKAGES}",
            "anastasia-run-python", "--script", params["script"]]


#: Where an install puts its packages, and where a script finds them. ONE
#: constant, because the two argv builders below must agree and a path spelled
#: twice is a path that drifts: an install into one directory and a script
#: reading another is "import numpy" failing after a green install.
SITE_PACKAGES = "/files/site-packages"


def _install_packages(params: dict) -> list:
    """pip, into the Capsule's files area, one name per argument.

    `env` FIRST, because the runner's rootfs is read-only and the image's
    default temp and cache locations are on it. TMPDIR is where pip unpacks
    wheels; HOME is where it would write a cache and a config it must not
    find; both point at /scratch, the one writable tmpfs every runner has.
    PIP_NO_CACHE_DIR keeps it from trying anyway.

    `--target` rather than a user or system install: nothing is written into
    the image, and the result is a directory a later job puts on its
    PYTHONPATH. `--upgrade` because a second install of a name already there
    is a request to replace it, not a no-op with a warning. `--no-input` and
    `--progress-bar off` because nobody is typing and the log is read as text.

    The proxy is not named here: a Capsule with egress hands every runner
    HTTPS_PROXY (executor/egress.py), and pip honours it. A Capsule without
    egress has no NIC, so pip fails at the first connection — loudly, which
    is the posture — but the app refuses such an install before it starts.
    """
    names = params["dists"].split("+")
    return [
        "env", "TMPDIR=/scratch", "HOME=/scratch", "PIP_NO_CACHE_DIR=1",
        "python", "-m", "pip", "install",
        "--disable-pip-version-check", "--no-input", "--progress-bar", "off",
        "--upgrade", "--target", SITE_PACKAGES,
        *names,
    ]


#: Where a LaTeX install puts its files, and where a compile finds them. One
#: constant for the same reason as SITE_PACKAGES: two spellings of one path
#: is "\usepackage{tcolorbox}" failing after a green install.
TEXMF_HOME = "/files/texmf"


def _install_latex_packages(params: dict) -> list:
    """CTAN packages into the Capsule's TeX tree, from the operator's mirror.

    Not tlmgr: Debian's TeX Live is a frozen apt snapshot whose tlmgr refuses
    to install (deploy/anastasia/latex/Dockerfile). The runner resolves each
    name through CTAN's package index to its TDS archive and unpacks that into
    TEXMF_HOME, which is exactly the layout TEXMFHOME expects. The same `env`
    reasons as pip's: a read-only rootfs, and /scratch the one writable temp.
    """
    names = params["packages"].split("+")
    return [
        "env", "TMPDIR=/scratch", "HOME=/scratch",
        "anastasia-install-latex",
        "--texmf", TEXMF_HOME,
        "--mirror", params["mirror"],
        *names,
    ]


_BUILDERS = {
    "render_pdf": _render_pdf,
    "install_packages": _install_packages,
    "install_latex_packages": _install_latex_packages,
    "compile_latex": _compile_latex,
    "run_media_command": _run_media_command,
    "run_ocr": _run_ocr,
    "run_python": _run_python,
}


def known_operations() -> frozenset:
    return frozenset(_BUILDERS)
