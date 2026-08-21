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


class ParamError(ValueError):
    """A parameter a person can be told about in one sentence."""


@dataclass(frozen=True)
class Param:
    name: str
    kind: str                      # "str" | "int" | "bool" | "enum" | "path" | "lang"
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

NORMALIZE_MEDIA = Operation(
    name="normalize_media", family=MEDIA, label="Normalize a media file",
    params=(
        Param("input", "path", required=True),
        # A named preset, never an ffmpeg argument string. The old
        # fileservices/manta path let a user type ffmpeg arguments and defended
        # itself by rejecting shell tokens; a fixed preset needs no such
        # defence because there is no user text on the command line at all.
        Param("preset", "enum", required=True,
              choices=("probe", "mp4_h264", "webm_vp9", "mp3", "wav",
                       "thumbnail", "gif")),
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

START_PYTHON_RUNTIME = Operation(
    name="start_python_runtime", family=PYTHON, label="Start a Python runtime",
    params=(
        # How long the runtime may sit unused before the manager reclaims it.
        # Bounded here; the host's own dial (dracena.kernel_idle) narrows it
        # further, and a warm Gear keeps it alive across that boundary.
        Param("idle_seconds", "int", default=3600, minimum=60, maximum=604800),
    ),
    # A runtime is not a job: the "timeout" is how long START may take, not how
    # long the kernel lives.
    default_timeout=120, max_timeout=300,
    outputs=("connection.json",),
)

OPERATIONS = {
    op.name: op for op in (
        RENDER_PDF, COMPILE_LATEX, NORMALIZE_MEDIA, RUN_OCR,
        START_PYTHON_RUNTIME,
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
