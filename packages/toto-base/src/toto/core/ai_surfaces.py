"""Where the assistant can be asked something, and what it may be asked.

**This lives in toto-base, and the assistant does not.** An editor declares its
surface in ``<app>/ai_surfaces.py``, and the editors are in toto-base
(``toto.editor``) and toto-works (``cyprian``, ``memo``, ``primula``,
``sketch``) — neither of which may depend on toto-ai. So the registry sits where
every declarer can already reach it, exactly as ``toto.quota.metrics.Metric``,
``toto.quota.fees.FeeSource`` and ``toto.vault.plugins.VaultEditorPlugin`` do:
the contract in base, the consumer optional. ``check_package_graph.py`` refuses
the other arrangement, and it is right to — a module-level import of an
optional wheel from a core one is an inverted dependency however careful the
autodiscovery is.

An **AiSurface** is one editor's opinion about what a selection IS — prose, code,
LaTeX, a spreadsheet range — and the actions that make sense on it. Registered
from ``<app>/ai_surfaces.py`` and autodiscovered, the same shape as
``metrics``, ``fees``, ``scanners``, ``entitlements`` and ``taxes``. Adding an
editor is then one small file plus two client functions, not a new endpoint and
not a new prompt-assembly path.

**The selection is the whole payload.** A surface never sends the document. That
is not a privacy gesture, it is the billing model: what you pay is measured in
tokens, and shipping a 60-page file to fix one sentence is the difference
between a free action and an expensive one. Where an editor genuinely needs
surrounding context it sends a bounded window, and says so.

**Actions describe, they do not execute.** An ``Action`` is a system prompt, an
instruction template and a declared output shape. The model is told what to
return; the surface's ``file_type`` decides whether the answer is screened by
``toto.vault.scanning`` before anybody is offered it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator


class DuplicateSurface(Exception):
    """Two apps claimed the same surface key."""


@dataclass(frozen=True)
class Action:
    """One thing the assistant can be asked to do to a selection."""

    key: str
    label: str
    icon: str = "fa-solid fa-wand-magic-sparkles"
    #: What the model is told it is. Short: a long persona costs tokens on every
    #: call and buys very little.
    system: str = ""
    #: ``{selection}`` and ``{instruction}`` are substituted; nothing else is.
    template: str = "{selection}"
    #: True when the action needs the user to type something (translate → which
    #: language, rewrite → how).
    needs_instruction: bool = False
    #: A hint rendered beside the input when it does.
    instruction_placeholder: str = ""


@dataclass(frozen=True)
class AiSurface:
    """One editor's ask-the-assistant contract."""

    key: str
    label: str
    #: prose | code | latex | sheet | cells. Decides the default system prompt
    #: and, with file_type, whether the answer is screened.
    kind: str
    actions: tuple = ()
    #: The vault file type an answer will end up inside, when it is one. Drives
    #: screening: an answer bound for an .html document is untrusted third-party
    #: content and goes through the same scanner an upload does.
    file_type: str = ""
    icon: str = "fa-solid fa-wand-magic-sparkles"
    description: str = ""

    def action(self, key: str) -> Action | None:
        for candidate in self.actions:
            if candidate.key == key:
                return candidate
        return None


class SurfaceRegistry:
    def __init__(self):
        self._items: dict[str, AiSurface] = {}

    def register(self, surface: AiSurface) -> AiSurface:
        existing = self._items.get(surface.key)
        if existing is not None and existing != surface:
            raise DuplicateSurface(
                f"{surface.key!r} is already registered by another app.")
        self._items[surface.key] = surface
        return surface

    def get(self, key: str) -> AiSurface | None:
        return self._items.get(key)

    def all(self) -> Iterator[AiSurface]:
        return iter(sorted(self._items.values(), key=lambda s: s.label))


registry = SurfaceRegistry()


# ---------------------------------------------------------------------------
# The shared action vocabulary
# ---------------------------------------------------------------------------
# Editors compose from these rather than inventing their own wording, so
# "Improve" means the same thing in a document and in a spreadsheet cell. A
# surface that needs something genuinely different declares its own Action.

_PROSE_SYSTEM = (
    "You edit text. Return ONLY the edited text, with no preamble, no "
    "explanation and no code fences. Keep the author's voice and language "
    "unless told otherwise. If the text is already good, return it unchanged."
)

_CODE_SYSTEM = (
    "You edit source code. Return ONLY code, with no prose, no explanation and "
    "no markdown fences. Preserve the surrounding style and indentation. Never "
    "invent an API you were not shown."
)


def prose_actions() -> tuple:
    return (
        Action("improve", "Improve", "fa-solid fa-wand-magic-sparkles",
               system=_PROSE_SYSTEM,
               template="Improve the clarity and flow of this text:\n\n{selection}"),
        Action("shorten", "Shorten", "fa-solid fa-compress",
               system=_PROSE_SYSTEM,
               template="Make this text shorter without losing meaning:\n\n{selection}"),
        Action("expand", "Expand", "fa-solid fa-up-right-and-down-left-from-center",
               system=_PROSE_SYSTEM,
               template="Expand this text with more detail, in the same voice:\n\n{selection}"),
        Action("translate", "Translate", "fa-solid fa-language",
               system=_PROSE_SYSTEM, needs_instruction=True,
               instruction_placeholder="Polish, German, plain English…",
               template="Translate this text into {instruction}:\n\n{selection}"),
        Action("rewrite", "Rewrite as…", "fa-solid fa-pen-nib",
               system=_PROSE_SYSTEM, needs_instruction=True,
               instruction_placeholder="a bullet list, a formal note…",
               template="Rewrite this text as {instruction}:\n\n{selection}"),
        Action("explain", "Explain", "fa-solid fa-circle-question",
               system=("You explain text. Answer in prose, briefly. This answer "
                       "is NOT a replacement for the text."),
               template="Explain this, briefly:\n\n{selection}"),
    )


def code_actions(language: str = "") -> tuple:
    what = f" {language}" if language else ""
    return (
        Action("explain", "Explain", "fa-solid fa-circle-question",
               system=(f"You explain{what} code. Answer in prose, briefly. This "
                       "answer is NOT a replacement for the code."),
               template="Explain what this code does, briefly:\n\n{selection}"),
        Action("comment", "Add comments", "fa-solid fa-comment",
               system=_CODE_SYSTEM,
               template=("Add brief comments to this{0} code explaining WHY, not "
                         "what. Change nothing else:\n\n{{selection}}").format(what)),
        Action("fix", "Find the bug", "fa-solid fa-bug",
               system=_CODE_SYSTEM,
               template=("Fix the bug in this{0} code. If there is no bug, return "
                         "it unchanged:\n\n{{selection}}").format(what)),
        Action("docstring", "Write a docstring", "fa-solid fa-file-lines",
               system=_CODE_SYSTEM,
               template=("Return this{0} code with a docstring added to the "
                         "top-level definition. Change nothing else:"
                         "\n\n{{selection}}").format(what)),
        Action("rewrite", "Rewrite as…", "fa-solid fa-pen-nib",
               system=_CODE_SYSTEM, needs_instruction=True,
               instruction_placeholder="a list comprehension, async…",
               template="Rewrite this{0} code as {{instruction}}:\n\n{{selection}}".format(what)),
    )


def build_messages(surface: AiSurface, action: Action, *, selection: str,
                   instruction: str = "") -> list:
    """The two-message payload. No history: an action is a single shot.

    The parked app resent an unbounded conversation on every turn, which grew
    the bill quadratically with the length of a chat nobody had read in an hour.
    An action needs exactly what it is acting on.
    """
    user = action.template.format(selection=selection,
                                  instruction=instruction or "")
    return [
        {"role": "system", "content": action.system or _PROSE_SYSTEM},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# The whole-file surface
# ---------------------------------------------------------------------------
# Registered here rather than in an app's ai_surfaces.py because the vault is
# the app that owns files, and the wand is offered from its listing. Its actions
# READ rather than rewrite: an answer about a 40-page document is a paragraph,
# not a replacement for the document, and offering "improve" over a whole file
# would propose something nobody can review in a modal.

registry.register(AiSurface(
    key="file",
    label="A whole file",
    kind="prose",
    icon="fa-solid fa-file-circle-question",
    description="Summarise, explain or check a whole file.",
    actions=(
        Action("summarise", "Summarise", "fa-solid fa-compress",
               system=("You summarise documents. Answer in prose, briefly. This "
                       "is NOT a replacement for the document."),
               template="Summarise this file:\n\n{selection}"),
        Action("explain", "Explain", "fa-solid fa-circle-question",
               system=("You explain documents and code. Answer in prose, "
                       "briefly. This is NOT a replacement for the file."),
               template="Explain what this file is and what it does:\n\n{selection}"),
        Action("review", "Find problems", "fa-solid fa-bug",
               system=("You review documents and code and report problems. "
                       "Answer in prose as a short list. Say so plainly if you "
                       "find nothing."),
               template="What is wrong or missing in this file?\n\n{selection}"),
        Action("ask", "Ask about it…", "fa-solid fa-comment",
               system=("You answer questions about a document. Answer in prose, "
                       "briefly, and only from what you were shown."),
               needs_instruction=True,
               instruction_placeholder="who signed this? what does line 40 do?",
               template="{instruction}\n\nThe file:\n\n{selection}"),
    ),
))
