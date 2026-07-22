"""
Abstract base for manta commands.

Each command (compress, probe, transcribe, …) is a small class in its own
file inheriting :class:`BaseCommand`. A command declares its input/output file
slots, its parameter form, and how to produce a preview:

* ``backend in ("ffmpeg", "ffprobe")`` → implement ``build_spec`` (delegates to
  the pure argv builders in ``manta/builders.py``).
* ``backend == "service"`` (transcribe) → implement ``describe`` and declare
  ``service_key``; execution reuses the fileservices FileServiceRun backend.

Subclasses self-register by ``key`` via ``__init_subclass__``.
"""

from __future__ import annotations

import os
import shlex
from abc import ABC


class UnknownCommand(ValueError):
    """Raised for an unregistered command key."""


class CommandSpec:
    """An immutable, fully-resolved command preview.

    ``commands`` holds one argv per ffmpeg/ffprobe invocation (two for gif).
    """

    def __init__(self, commands, output_names=()):
        self.commands = tuple(tuple(c) for c in commands)
        self.output_names = tuple(output_names)

    @property
    def shell_display(self) -> str:
        return "\n".join(shlex.join(list(c)) for c in self.commands)


_REGISTRY: dict[str, type["BaseCommand"]] = {}


class BaseCommand(ABC):
    key: str = ""
    label: str = ""
    backend: str = "ffmpeg"          # "ffmpeg" | "ffprobe" | "service"
    backend_label: str = ""          # tool shown in the UI (defaults to backend)
    service_key: str = ""            # for backend == "service"
    tab: str = "ffmpeg"              # which builder tab the command lives under
    inputs: dict = {}
    outputs: dict = {}
    form_class = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if getattr(cls, "key", ""):
            _REGISTRY[cls.key] = cls

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def output_name(params) -> str:
        return (params or {}).get("output_name") or "output"

    @staticmethod
    def secondary(extra_input_names) -> str:
        names = extra_input_names or []
        return names[0] if names else "second.mp4"

    # ffmpeg / ffprobe commands implement this:
    def build_spec(self, *, input_name, extra_input_names=None, params=None) -> CommandSpec:
        raise NotImplementedError

    # service commands implement this (the "command" shown in the UI):
    def describe(self, *, input_name, params=None) -> str:
        return f"{self.label}: {input_name}"

    # backend base classes implement this (they own execution):
    def execute(self, job) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------ registry
    @classmethod
    def get(cls, key) -> type["BaseCommand"]:
        try:
            return _REGISTRY[key]
        except KeyError:
            raise UnknownCommand(f"Unknown command: {key!r}")

    @classmethod
    def all(cls) -> list[type["BaseCommand"]]:
        return list(_REGISTRY.values())
