"""What a caller is allowed to ask for, and nothing else.

This module IS the security boundary. A caller names an **operation** and
supplies **declared parameters**; the manager looks the operation up here and
builds the container itself. There is deliberately no way to express an image,
a mount, a flag, a capability, a command, a network or privileged mode — not
because the manager filters those out, but because the vocabulary has no word
for them.

Django-free: the caller validates against this catalogue to refuse early, and
the manager validates against the same catalogue to refuse authoritatively.
One table, read twice, so an early refusal and a late one can never disagree.

Adding an operation is a commit here plus a runner image. That is the intended
friction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .limits import Limits

# --------------------------------------------------------------------------- #
# Parameter declarations                                                       #
# --------------------------------------------------------------------------- #

#: A staged input path. Relative, no traversal, no absolute paths, no NUL. The
#: manager re-checks on extraction as well — this is the early, friendly half.
_SAFE_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[\w][\w .+@/-]{0,199}$")

#: A tesseract language spec: "eng", "pol", "eng+pol". Nothing else may reach a
#: command line that is assembled from it.
_SAFE_LANG = re.compile(r"^[a-z]{3}(\+[a-z]{3}){0,3}$")

#: A plain name — an output stem, never a path and never an extension.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,59}$")

#: An ffmpeg time position: seconds, or HH:MM:SS with optional milliseconds.
#: Bounded because it is interpolated into an argv, and because "whatever
#: ffmpeg accepts" is a much larger surface than anything a form needs.
_SAFE_TIME = re.compile(r"^(\d{1,2}:\d{2}:\d{2}(\.\d{1,3})?|\d{1,6}(\.\d{1,3})?)$")


class ParamError(ValueError):
    """A parameter a person can be told about in one sentence."""


@dataclass(frozen=True)
class Param:
    name: str
    kind: str      # "str"|"int"|"bool"|"enum"|"path"|"lang"|"time"
    required: bool = False
    default: object = None
    choices: tuple = ()
    minimum: int = 0
    maximum: int = 0
    max_length: int = 200

    def clean(self, raw):
        """Return the validated value, or raise :class:`ParamError`."""
        if raw is None:
            if self.required:
                raise ParamError(f"{self.name} is required.")
            return self.default

        if self.kind == "bool":
            if not isinstance(raw, bool):
                raise ParamError(f"{self.name} must be true or false.")
            return raw

        if self.kind == "int":
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ParamError(f"{self.name} must be a whole number.")
            if raw < self.minimum or raw > self.maximum:
                raise ParamError(
                    f"{self.name} must be between {self.minimum} and "
                    f"{self.maximum} (got {raw}).")
            return raw

        if not isinstance(raw, str):
            raise ParamError(f"{self.name} must be text.")
        if len(raw) > self.max_length:
            raise ParamError(
                f"{self.name} is longer than {self.max_length} characters.")

        if self.kind == "enum":
            if raw not in self.choices:
                raise ParamError(
                    f"{self.name} must be one of: {', '.join(self.choices)}.")
            return raw
        if self.kind == "path":
            if not _SAFE_PATH.match(raw):
                raise ParamError(
                    f"{self.name} must be a relative path inside the staged "
                    "input, with no “..” segments.")
            return raw
        if self.kind == "str":
            # A NAME, and it becomes a filename in the runner's scratch — so
            # the safe set is deliberately narrower than "text": letters,
            # digits, and the three separators a person actually types. No
            # dots (an extension is the runner's to choose), no slashes, no
            # spaces. Without this the kind fell through to "unknown parameter
            # kind" and every command carrying one was refused.
            if not _SAFE_NAME.match(raw):
                raise ParamError(
                    f"{self.name} may contain only letters, digits, dashes "
                    "and underscores.")
            return raw
        if self.kind == "time":
            if not _SAFE_TIME.match(raw):
                raise ParamError(
                    f"{self.name} must be a time like “00:01:30” or a number "
                    "of seconds.")
            return raw
        if self.kind == "lang":
            if not _SAFE_LANG.match(raw):
                raise ParamError(
                    f"{self.name} must be language codes like “eng” or "
                    "“eng+pol”.")
            return raw
        raise ParamError(f"{self.name} has an unknown parameter kind.")


# --------------------------------------------------------------------------- #
# Families — one runner image each                                             #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Family:
    key: str
    label: str
    image: str
    #: What one execution of this family gets if the caller names no limits.
    #: Always bounded by the Gear it runs in, so these are starting points and
    #: not guarantees.
    default_limits: Limits
    #: Whether a runner of this family may be kept alive between executions
    #: inside a mounted Gear (the warm_policy). Batch families are cheap to
    #: recreate and gain little; the python family's warm runtime IS the live
    #: kernel, which is the whole point of keeping it.
    warmable: bool = False
    #: Batch runners get no network at all. The python family needs the Gear's
    #: internal network so the session owner can reach the kernel's ports —
    #: that network reaches no database and no broker.
    needs_internal_network: bool = False
    #: The third posture, and the only one that can reach the internet.
    #:
    #: Nothing that runs USER CODE may set this. It exists for one shape of job:
    #: fetching declared packages from a package index, in a throwaway container
    #: with a fixed argv and nothing of the user's resident in it. That is what
    #: makes egress defensible here and not in a kernel — a kernel runs whatever
    #: somebody types, and giving that egress is an exfiltration path out of a
    #: sandbox built to have none.
    #:
    #: Still a property of the FAMILY, declared here, never a parameter: a
    #: caller names an operation and the operation carries its posture. There is
    #: deliberately no way to ask for a network.
    needs_egress: bool = False


PDF = Family(
    key="pdf", label="PDF rendering", image="anastasia-pdf",
    default_limits=Limits(cpu_millicores=1000, ram_mb=512, scratch_mb=256, pids=64),
)
LATEX = Family(
    key="latex", label="LaTeX", image="anastasia-latex",
    # LaTeX is the heaviest batch family: a first compile builds font caches and
    # latex-extra documents are memory-hungry.
    default_limits=Limits(cpu_millicores=1000, ram_mb=1024, scratch_mb=512, pids=128),
)
MEDIA = Family(
    key="media", label="Media processing", image="anastasia-media",
    # ffmpeg threads: more CPU, and scratch large enough for one output beside
    # one input.
    default_limits=Limits(cpu_millicores=2000, ram_mb=1024, scratch_mb=2048, pids=128),
)
OCR = Family(
    key="ocr", label="OCR", image="anastasia-ocr",
    default_limits=Limits(cpu_millicores=1000, ram_mb=512, scratch_mb=128, pids=64),
)
PYTHON = Family(
    key="python", label="Python runtime", image="anastasia-python",
    default_limits=Limits(cpu_millicores=1000, ram_mb=1024, scratch_mb=512, pids=128),
    warmable=True, needs_internal_network=True,
)
#: The same image as PYTHON, and deliberately so — packages are installed by the
#: interpreter that will import them, so a wheel chosen here cannot be one the
#: kernel then refuses. What differs is the posture: egress, no warm reuse, and
#: a batch lifetime measured in seconds.
PYTHON_INSTALL = Family(
    key="python-install", label="Python package install", image="anastasia-python",
    # Roomier than the runtime: resolving and unpacking wheels is short but
    # memory-hungry, and the tree being built has to fit in scratch beside them.
    default_limits=Limits(cpu_millicores=2000, ram_mb=2048, scratch_mb=2048, pids=256),
    warmable=False, needs_egress=True,
)
#: A kernel for a workspace whose owner asked for a CONNECTED one. Same image
#: and same limits as PYTHON; the only difference is that user code can reach
#: the network. A separate family rather than a flag on PYTHON because that is
#: what keeps the posture declared here instead of chosen by a caller.
PYTHON_CONNECTED = Family(
    key="python-connected", label="Python runtime (connected)",
    image="anastasia-python",
    default_limits=Limits(cpu_millicores=1000, ram_mb=1024, scratch_mb=512, pids=128),
    warmable=True, needs_egress=True,
)

FAMILIES = {f.key: f for f in (PDF, LATEX, MEDIA, OCR, PYTHON,
                               PYTHON_INSTALL, PYTHON_CONNECTED)}


def family(key: str) -> Family:
    try:
        return FAMILIES[key]
    except KeyError:
        raise ParamError(
            f"“{key}” is not a runner family. The families are: "
            f"{', '.join(sorted(FAMILIES))}."
        ) from None


# --------------------------------------------------------------------------- #
# Operations — what a caller may actually ask for                              #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Operation:
    name: str
    family: Family
    label: str
    params: tuple = ()
    #: Wall-clock ceiling in seconds. The manager kills past it; the caller's
    #: celery soft_time_limit must exceed it or the worker dies first and the
    #: sweeper has to clean up what a timeout should have.
    default_timeout: int = 120
    max_timeout: int = 3600
    #: Relative paths the runner is expected to leave in /out. Documentation
    #: for the caller; the manager returns whatever is there, bounded.
    outputs: tuple = ()

    def clean(self, params: dict | None) -> dict:
        """Validate an untrusted parameter dict against this operation."""
        params = params or {}
        if not isinstance(params, dict):
            raise ParamError("Operation parameters must be a mapping.")
        declared = {p.name for p in self.params}
        unknown = set(params) - declared
        if unknown:
            raise ParamError(
                f"{self.name} does not take {', '.join(sorted(unknown))}. "
                f"It takes: {', '.join(sorted(declared)) or 'no parameters'}."
            )
        return {p.name: p.clean(params.get(p.name)) for p in self.params}

    def clean_timeout(self, seconds) -> int:
        if seconds is None:
            return self.default_timeout
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            raise ParamError("timeout must be a whole number of seconds.")
        if seconds < 1 or seconds > self.max_timeout:
            raise ParamError(
                f"timeout must be between 1 and {self.max_timeout} seconds "
                f"for {self.name}.")
        return seconds


RENDER_PDF = Operation(
    name="render_pdf", family=PDF, label="Render HTML to PDF",
    # No parameters at all, deliberately. The HTML is staged as input.html and
    # WeasyPrint is invoked with base_url=None inside the runner, so a document
    # cannot fetch anything — the same closure aralia's render.py documents,
    # now enforced by a network-less container as well.
    params=(),
    default_timeout=120, max_timeout=600,
    outputs=("output.pdf",),
)

COMPILE_LATEX = Operation(
    name="compile_latex", family=LATEX, label="Compile a LaTeX project",
    params=(
        Param("main", "path", required=True),
        Param("engine", "enum", default="pdflatex",
              choices=("pdflatex", "xelatex", "lualatex")),
        Param("max_passes", "int", default=3, minimum=1, maximum=5),
        Param("use_latexmk", "bool", default=True),
    ),
    default_timeout=180, max_timeout=1800,
    outputs=("output.pdf", "output.log"),
)

#: What manta's command builder offers, by key. Each one maps to a pure argv
#: builder in the runner image (``anastasia_runner.ffmpeg_builders``), and the
#: parameters below are what that builder takes.
#:
#: This is the whole vocabulary — there is no "other" and no escape hatch. The
#: fileservices path this replaces let a user type ffmpeg arguments and
#: defended itself by rejecting shell metacharacters, which is a defence that
#: has to be right every time. A closed set of commands with typed parameters
#: needs no such defence: there is no user text on the command line at all.
MEDIA_COMMANDS = (
    "probe", "compress", "resize", "cut", "extract_mp3", "thumbnail", "gif",
    "concat", "crop", "change_fps", "remove_audio", "replace_audio",
    "add_subtitles", "add_watermark", "vstack", "hstack",
)

#: ffmpeg time positions: "90", "00:01:30", "00:01:30.5". Anything else is
#: refused rather than passed through for ffmpeg to interpret.
_TIME = r"^\d{1,2}:\d{2}:\d{2}(\.\d{1,3})?$|^\d{1,6}(\.\d{1,3})?$"

RUN_MEDIA_COMMAND = Operation(
    name="run_media_command", family=MEDIA, label="Run a media command",
    params=(
        Param("command", "enum", required=True, choices=MEDIA_COMMANDS),
        Param("input", "path", required=True),
        #: A second input, for the commands that take one (replace_audio's
        #: audio track, add_watermark's image, concat's extra files,
        #: add_subtitles' subtitle file, the stack commands' second video).
        Param("second", "path"),
        Param("output_name", "str", default="output", max_length=60),

        # Geometry. -2 is ffmpeg's "compute from the other dimension keeping
        # the aspect ratio", which is why the minimum is negative.
        Param("width", "int", default=0, minimum=-2, maximum=16384),
        Param("height", "int", default=0, minimum=-2, maximum=16384),
        Param("x", "int", default=0, minimum=0, maximum=16384),
        Param("y", "int", default=0, minimum=0, maximum=16384),

        Param("quality", "enum", default="medium",
              choices=("tiny", "small", "medium", "high", "archive")),
        Param("bitrate", "enum", default="192k",
              choices=("64k", "96k", "128k", "192k", "256k", "320k")),
        Param("fps", "int", default=0, minimum=1, maximum=240),

        Param("start_time", "time", default="00:00:00"),
        Param("end_time", "time"),
        Param("duration", "time", default="5"),

        Param("position", "enum", default="bottom_right",
              choices=("top_left", "top_right", "bottom_left",
                       "bottom_right", "center")),
        Param("reencode", "bool", default=False),
    ),
    default_timeout=900, max_timeout=7200,
    outputs=("output.*", "probe.json"),
)

RUN_OCR = Operation(
    name="run_ocr", family=OCR, label="Read text from an image",
    params=(
        Param("input", "path", required=True),
        Param("lang", "lang", default="eng"),
        # Tesseract page-segmentation mode. Bounded to the documented range so
        # the value can be interpolated into argv without further thought.
        Param("psm", "int", default=3, minimum=0, maximum=13),
    ),
    default_timeout=120, max_timeout=900,
    outputs=("output.json", "output.txt"),
)

INSTALL_PYTHON_PACKAGES = Operation(
    name="install_python_packages", family=PYTHON_INSTALL,
    label="Install Python packages",
    # NO package names among the parameters, deliberately — the same choice
    # render_pdf makes for its HTML. The caller stages a `requirements.txt` into
    # the input area and pip is pointed at it with `-r`, so a package name never
    # reaches a command line at all. There is consequently nothing here to
    # escape, quote or validate against shell metacharacters: the class of bug
    # is absent rather than defended against.
    #
    # It also means the thing pip was asked for and the thing we persist as the
    # workspace manifest are the same bytes, and cannot drift.
    params=(
        # Whether to move already-satisfied requirements forward. Off by
        # default: an install should add what was asked for and change nothing
        # else, or "add one package" silently becomes "upgrade the world".
        Param("upgrade", "bool", default=False),
    ),
    # Generous, because a cold wheel fetch over a slow link is the normal case
    # and being killed halfway leaves the user nothing.
    default_timeout=600, max_timeout=3600,
    outputs=("site-packages", "install.log", "installed.json"),
)

START_PYTHON_RUNTIME = Operation(
    name="start_python_runtime", family=PYTHON, label="Start a Python runtime",
    params=(
        # How long the runtime may sit unused before the manager reclaims it.
        # Bounded here; the host's own dial (dracena.kernel_idle) narrows it
        # further, and a warm Gear keeps it alive across that boundary.
        Param("idle_seconds", "int", default=3600, minimum=60, maximum=604800),
        # Whether HOME lives in the OUTPUT area instead of scratch. Scratch is
        # a per-execution tmpfs, so anything a tool writes to $HOME — .ipython
        # history, .jupyter config, a `pip --user` install — dies with the
        # container. /out is a bind mount the caller collects, so the same
        # writes survive and can be staged back on the next start.
        #
        # An ordinary typed parameter, not a container flag: it changes one
        # environment variable inside the runner and nothing about how the
        # container is built.
        Param("persistent_home", "bool", default=False),
    ),
    # A runtime is not a job: the "timeout" is how long START may take, not how
    # long the kernel lives.
    default_timeout=120, max_timeout=300,
    outputs=("connection.json",),
)

#: The same runtime, for a workspace whose owner chose a connected Gear. Two
#: operations rather than a `connected` parameter, because a parameter would be
#: a caller asking for a network — the one thing this catalogue has no word for.
START_PYTHON_RUNTIME_CONNECTED = Operation(
    name="start_python_runtime_connected", family=PYTHON_CONNECTED,
    label="Start a Python runtime (connected)",
    params=(
        Param("idle_seconds", "int", default=3600, minimum=60, maximum=604800),
        # Whether HOME lives in the OUTPUT area instead of scratch. Scratch is
        # a per-execution tmpfs, so anything a tool writes to $HOME — .ipython
        # history, .jupyter config, a `pip --user` install — dies with the
        # container. /out is a bind mount the caller collects, so the same
        # writes survive and can be staged back on the next start.
        #
        # An ordinary typed parameter, not a container flag: it changes one
        # environment variable inside the runner and nothing about how the
        # container is built.
        Param("persistent_home", "bool", default=False),
    ),
    default_timeout=120, max_timeout=300,
    outputs=("connection.json",),
)

OPERATIONS = {
    op.name: op for op in (
        RENDER_PDF, COMPILE_LATEX, RUN_MEDIA_COMMAND, RUN_OCR,
        START_PYTHON_RUNTIME, START_PYTHON_RUNTIME_CONNECTED,
        INSTALL_PYTHON_PACKAGES,
    )
}


def operation(name: str) -> Operation:
    try:
        return OPERATIONS[name]
    except KeyError:
        raise ParamError(
            f"“{name}” is not an Anastasia operation. The operations are: "
            f"{', '.join(sorted(OPERATIONS))}."
        ) from None


def operations_for(family_key: str) -> tuple:
    return tuple(op for op in OPERATIONS.values() if op.family.key == family_key)


__all__ = [
    "Family", "Operation", "Param", "ParamError",
    "FAMILIES", "OPERATIONS", "family", "operation", "operations_for",
    "PDF", "LATEX", "MEDIA", "OCR", "PYTHON",
    "PYTHON_INSTALL", "PYTHON_CONNECTED",
]
