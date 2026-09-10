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

#: A wheelhouse selection: PEP 503 normalised distribution names, joined by
#: "+" the way _SAFE_LANG joins OCR languages. NO VERSIONS AND NO OPERATORS —
#: the wheelhouse pins exactly one build of each distribution, so a version in
#: a request could only ever agree with the pin or contradict it, and a
#: requirement specifier is a small language nobody needs here. It is also what
#: keeps this out of argv-injection territory: the names go to pip as separate
#: arguments after --no-index, and nothing else can be spelled.
_SAFE_DISTS = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?"
                         r"(\+[a-z0-9]([a-z0-9._-]*[a-z0-9])?)*$")

#: An ffmpeg time position: seconds, or HH:MM:SS with optional milliseconds.
#: Bounded because it is interpolated into an argv, and because "whatever
#: ffmpeg accepts" is a much larger surface than anything a form needs.
_SAFE_TIME = re.compile(r"^(\d{1,2}:\d{2}:\d{2}(\.\d{1,3})?|\d{1,6}(\.\d{1,3})?)$")


class ParamError(ValueError):
    """A parameter a person can be told about in one sentence."""


@dataclass(frozen=True)
class Param:
    name: str
    kind: str      # "str"|"int"|"bool"|"enum"|"path"|"lang"|"time"|"dists"
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
        if self.kind == "dists":
            # FULL PEP 503 NORMALISATION, not just a lower(). The spec says
            # names compare case-insensitively AND that any run of -, _ or .
            # collapses to a single -, so "NumPy", "numpy" and "num_py" are one
            # distribution and "numpy--x" is "numpy-x". Doing it here, on the
            # trusted side, is what lets the check against the wheelhouse
            # manifest be a plain string equality; lower-casing alone would let
            # two spellings of one package through as two, and the duplicate
            # check below would not see them.
            raw = re.sub(r"[-_.]+", "-", raw.lower())
            if not _SAFE_DISTS.match(raw):
                raise ParamError(
                    f"{self.name} must be package names like “numpy” or "
                    "“numpy+pandas”, with no versions and no spaces.")
            names = raw.split("+")
            if len(set(names)) != len(names):
                raise ParamError(f"{self.name} names the same package twice.")
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
    #: Always bounded by the Capsule it runs in, so these are starting points and
    #: not guarantees.
    default_limits: Limits
    #: TWO postures, and there is deliberately no third.
    #:
    #: Batch runners get NO network at all. The python family gets one link and
    #: one only: the Capsule's internal network, so the session owner can reach the
    #: kernel's ports. That network reaches no database, no broker and no
    #: internet.
    #:
    #: EGRESS IS GONE (2026-09-10). A `needs_egress` posture existed for one
    #: NO NETWORK FIELD AT ALL, since 2026-09-10. Two have been deleted here.
    #:
    #: `needs_egress` was first: one shape of job — fetching declared packages
    #: from an index in a throwaway container — and the only way anything in a
    #: Capsule could reach the internet. Both families that declared it are
    #: deleted and dependencies are baked into the runner images.
    #:
    #: `kernel_link` was second. It put the python family on the Capsule's
    #: internal network so the web tier could reach a long-lived kernel's ZMQ
    #: ports. There is no kernel: a Run is one job that writes its output and
    #: exits, and nothing needs to connect TO a runner.
    #:
    #: With both gone the posture collapses to one case — every runner of every
    #: family gets `--network none` — and that is why the field is removed
    #: rather than left False everywhere. A posture nothing can request is one
    #: nobody has to reason about, and an unused field is one somebody sets.


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
    key="python", label="Python", image="anastasia-python",
    default_limits=Limits(cpu_millicores=1000, ram_mb=1024, scratch_mb=512, pids=128),
)
FAMILIES = {f.key: f for f in (PDF, LATEX, MEDIA, OCR, PYTHON)}


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

RUN_PYTHON = Operation(
    name="run_python", family=PYTHON, label="Run a Python script",
    params=(
        # WHERE the script was staged, not the script itself. Source arrives
        # as staged input like any other payload: an argv has a length limit
        # measured in kilobytes, and a script passed as an argument would show
        # up in `ps` for every process on the host.
        Param("script", "path", default="main.py"),
    ),
    # A real job timeout now, not "how long may START take". The runtime this
    # replaces was the one long-lived family; nothing here outlives its job.
    default_timeout=120, max_timeout=900,
    # Whatever the script wrote. Empty rather than a fixed name, because a run
    # that produces nothing is ordinary and demanding a file would fail it.
    outputs=(),
)

OPERATIONS = {
    op.name: op for op in (
        RENDER_PDF, COMPILE_LATEX, RUN_MEDIA_COMMAND, RUN_OCR,
        RUN_PYTHON,
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
]
