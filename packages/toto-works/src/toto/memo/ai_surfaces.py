"""What the assistant may be asked about a slide's prose — and about a deck.

`file_type="html"` because a memo block's payload IS HTML — the same reason
cyprian's surface is screened. What differs is the UNIT: memo's blocks are
already addressable (each carries its own `payload`, `id` and `slot`), so the
natural thing to act on is a block, and the client offers the whole block when
nothing narrower is selected.

The DECK is a second surface, not a second action: its fragment grammar is not
HTML. A generated slide arrives as plain text (title line, bullet lines) that
the client escapes and builds into model blocks itself — which is why the
element action is DECLARED here (declared beats synthesised, see
``resolve_action``) and why there is no ``file_type``: screening plain text as
HTML is the false-alarm trap ``editor/ai_surfaces.py`` documents.
"""

from toto.core.ai_surfaces import (ELEMENT_ACTION, Action, AiSurface,
                                   prose_actions, registry)

registry.register(AiSurface(
    key="memo",
    label="Presentations",
    kind="prose",
    file_type="html",
    icon="fa-solid fa-display",
    description="Rewrite, shorten, translate or explain the text in a block.",
    actions=prose_actions(),
))

#: The one action every vocabulary must carry — the chip counts on it.
_ASK = next(a for a in prose_actions() if a.key == "ask")

registry.register(AiSurface(
    key="memo-deck",
    label="Presentation decks",
    kind="prose",
    icon="fa-solid fa-display",
    description="Generate a new slide from an instruction.",
    actions=(
        Action(ELEMENT_ACTION, "Generate a slide", "fa-solid fa-plus",
               system=("You write one presentation slide. Return ONLY plain "
                       "text: the first line is the slide title; every "
                       "following line is one short bullet point starting "
                       "with '- '. No HTML, no Markdown headings, no code "
                       "fences, no commentary before or after."),
               needs_instruction=True,
               instruction_placeholder="a slide about Q3 results…",
               template=("{instruction}\n\nThe deck so far, as an outline for "
                         "context only. Do not repeat it — return ONLY the "
                         "new slide.\n\n{selection}")),
        _ASK,
    ),
))
