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

**An operator's tuning arrives as an :class:`AgentVoice`.** Who the assistant is
and how the house wants it to write are configuration, not code — but they are
configuration held by an app this module may not import. So the voice is a plain
dataclass the caller passes in, and :func:`compose_system` decides where each
piece lands relative to the action's own rule. See that function for why the
action's rule goes last.
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
        # Every vocabulary carries "ask", because the side panel needs ONE
        # action it can count on being there whatever editor it is docked to.
        Action("ask", "Ask about it…", "fa-solid fa-comment",
               system=("You answer questions about a document. Answer in prose, "
                       "briefly, and only from what you were shown."),
               needs_instruction=True,
               instruction_placeholder="what is this about?",
               template="{instruction}\n\nThe text:\n\n{selection}"),
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
        Action("ask", "Ask about it…", "fa-solid fa-comment",
               system=(f"You answer questions about{what} code. Answer in prose, "
                       "briefly, and only from what you were shown."),
               needs_instruction=True,
               instruction_placeholder="what does this return?",
               template="{instruction}\n\nThe code:\n\n{selection}"),
    )


# ---------------------------------------------------------------------------
# Rewriting a whole document
# ---------------------------------------------------------------------------
# A different job from the selection actions above, and it needs its own rule.
# A selection action edits prose and hands back prose. This one is asked to
# return a COMPLETE, VALID source file in the document's own language — the
# answer replaces everything, so a truncated or fenced reply does not degrade,
# it destroys the document. Hence the emphasis, and hence the caller pins the
# language rather than letting the model infer it from what it was shown.

#: The one action a document surface offers. Free text in, a whole file out.
DOCUMENT_ACTION = "rewrite_document"

_DOCUMENT_SYSTEM = (
    "You rewrite a complete source document. Return ONLY the full new document "
    "and nothing else: no preamble, no explanation, no markdown code fences, no "
    "commentary before or after. The output REPLACES the file entirely, so it "
    "must be complete and valid on its own — never truncate, never abbreviate "
    "with an ellipsis or a 'rest unchanged' note. Preserve everything the "
    "instruction does not ask you to change, including formatting and comments."
)

#: What we call each file type when telling the model what to emit. Keys are
#: ``AiSurface.file_type`` / vault file types.
LANGUAGE_NAMES = {
    "html": "HTML",
    "xml": "XML",
    "json": "JSON",
    "svg": "SVG",
    "yaml": "YAML",
    "csv": "CSV",
    "latex": "LaTeX",
    "python": "Python",
    "bib": "BibTeX",
    "text": "plain text",
}


def resolve_action(surface: AiSurface, key: str) -> Action | None:
    """One place both the view and the worker look an action up.

    The document action is SYNTHESISED from the surface rather than declared by
    each editor: every surface would otherwise carry an identical copy, and the
    only thing that varies is the language, which the surface already knows.
    Resolved here so the endpoint that starts a run and the worker that executes
    it cannot disagree about what the run means.

    The element action is the OPPOSITE precedence on purpose: a declared one
    wins and synthesis is only the fallback. "Add an element" means a fragment
    of the file's own markup for a source document, but a memo deck wants a
    plain-text slide and a drawing wants a whole ``<svg>`` of new shapes — a
    surface whose fragment grammar is not "a fragment of file_type" declares
    its own Action under this key and the synthesised wording never fires.
    """
    if key == DOCUMENT_ACTION:
        return document_action(surface.file_type)
    if key == ELEMENT_ACTION:
        return surface.action(ELEMENT_ACTION) or element_action(surface.file_type)
    return surface.action(key)


def document_action(language: str = "") -> Action:
    """The rewrite-everything action, told which language to emit.

    ``language`` is the surface's ``file_type``. Naming it explicitly matters:
    asked to "rewrite this", a model shown a fragment of HTML will happily
    answer in Markdown, and the answer would replace an .html file.
    """
    named = LANGUAGE_NAMES.get(language, language)
    emit = f" Return valid {named}." if named else ""
    return Action(
        DOCUMENT_ACTION, "Rewrite the document", "fa-solid fa-file-pen",
        system=_DOCUMENT_SYSTEM + emit,
        needs_instruction=True,
        instruction_placeholder="what should change?",
        template=("{instruction}\n\nThe complete current document follows. "
                  "Return the complete new one.\n\n{selection}"),
    )


# ---------------------------------------------------------------------------
# Adding one new element
# ---------------------------------------------------------------------------
# The third job, and the toolbar's DEFAULT one. A rewrite replaces; this adds.
# The answer is a fragment the editor appends through its own command path, so
# the rule leans the other way from the document rule: never the whole file,
# only the new piece — an answer that re-emits the document would be pasted
# into it twice.

#: The add-an-element action. Synthesised like DOCUMENT_ACTION, but a surface
#: MAY declare its own Action under this key, and the declared one wins — see
#: resolve_action. It appears in no action list either; both sides agree on
#: the name, and steven/actions.js carries the same constant.
ELEMENT_ACTION = "generate_element"

_ELEMENT_SYSTEM = (
    "You add ONE new element to an existing document. Return ONLY the new "
    "element and nothing else: never the whole document, no preamble, no "
    "explanation, no markdown code fences, no commentary before or after. The "
    "output is inserted into the document exactly as returned, so it must be a "
    "complete, valid fragment on its own."
)


def element_action(language: str = "") -> Action:
    """The add-an-element action, told which language the fragment is in.

    ``language`` is the surface's ``file_type``, exactly as for
    :func:`document_action` and for the same reason: shown a fragment of HTML
    and asked to "add a section", a model will happily answer in Markdown.
    """
    named = LANGUAGE_NAMES.get(language, language)
    emit = f" Return a valid {named} fragment." if named else ""
    return Action(
        ELEMENT_ACTION, "Add to the document", "fa-solid fa-plus",
        system=_ELEMENT_SYSTEM + emit,
        needs_instruction=True,
        instruction_placeholder="what should be added?",
        template=("{instruction}\n\nThe current document follows, as context "
                  "only. Do not repeat it — return ONLY the new element.\n\n"
                  "{selection}"),
    )


# ---------------------------------------------------------------------------
# The operator's half of the system prompt
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentVoice:
    """Who the assistant is and how the house wants it to write.

    Configuration, edited by an operator, held by an app this module may not
    import — so it arrives as a plain value rather than a lookup. A caller with
    nothing configured passes ``None`` and gets the untouched action prompt,
    which is what every host does until somebody opens the management page.
    """

    #: What it calls itself. Rendered in the panel; also told to the model, so
    #: "who are you" gets the house answer rather than the provider's.
    name: str = ""
    #: One or two sentences: what it is and what it is for.
    persona: str = ""
    #: Blank means "answer in the language of the text" — the sane default for a
    #: platform whose users write in several. A value here overrides it.
    language: str = ""
    #: Rules that apply everywhere: what never to invent, how blunt to be.
    house_rules: str = ""
    #: kind → an extra note for surfaces of that kind only. Tuning how LaTeX
    #: answers come back should not change how prose answers do.
    kind_notes: dict = field(default_factory=dict)


def compose_system(surface: AiSurface, action: Action,
                   voice: AgentVoice | None = None, *,
                   personalization: str = "") -> str:
    """Assemble the system message. **The action's own rule always goes last.**

    Order is persona, then language, then the note for this kind of surface,
    then the house rules, then the user's personalization, then the action's
    rule — and that last position is not cosmetic. An action's rule is what
    says *return only the edited text, no preamble, no code fences*, and Accept
    pastes whatever comes back straight into somebody's document. An operator
    tuning tone must not be able to make the assistant start returning "Sure!
    Here you go:" in front of a paragraph, or ```` ``` ```` fences inside an
    HTML file. Everything above the action's rule shapes the voice; the
    action's rule fixes the shape of the answer, and it is stated closest to
    the question so it is the instruction in force.

    ``personalization`` is the asking USER's standing note — the per-person
    counterpart of the operator's voice, held by an app this module may not
    import, so it arrives as a plain string exactly as the voice arrives as a
    plain value. It sits under the house rules and above the action's rule for
    the same reason the voice does: a user's preferences shape tone and detail,
    and must be exactly as unable as the operator to displace the output rule.
    """
    action_rule = action.system or _PROSE_SYSTEM
    personal = (f"The user asking has set these standing preferences:\n"
                f"{personalization.strip()}" if personalization.strip() else "")
    if voice is None:
        parts = [personal, action_rule]
        return "\n\n".join(part for part in parts if part)

    who = voice.persona
    if voice.name and not who:
        who = f"You are {voice.name}."
    elif voice.name and voice.name.lower() not in who.lower():
        who = f"You are {voice.name}. {who}"

    language = (f"Always answer in {voice.language}." if voice.language else "")

    parts = [who, language, voice.kind_notes.get(surface.kind, ""),
             voice.house_rules, personal, action_rule]
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def build_messages(surface: AiSurface, action: Action, *, selection: str,
                   instruction: str = "", voice: AgentVoice | None = None,
                   personalization: str = "") -> list:
    """The two-message payload. No history: an action is a single shot.

    The parked app resent an unbounded conversation on every turn, which grew
    the bill quadratically with the length of a chat nobody had read in an hour.
    An action needs exactly what it is acting on.
    """
    user = action.template.format(selection=selection,
                                  instruction=instruction or "")
    return [
        {"role": "system",
         "content": compose_system(surface, action, voice,
                                   personalization=personalization)},
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
