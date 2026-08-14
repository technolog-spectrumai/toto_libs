"""What the assistant may add to a drawing.

``file_type="svg"`` is the load-bearing line: every answer is screened by
``toto.vault.scanning`` as SVG before anyone is offered it — the same scanner
this app hard-requires for every save (``sketch/apps.py`` fails ``check``
without toto.antivirus). The client then re-parses with its own parser and
falls back to one opaque shape, exactly as it does for an imported file.

The element action is DECLARED (declared beats synthesised — see
``resolve_action``) because a drawing's fragment grammar is not "a fragment of
svg": ``svgdoc.parse`` wants a complete ``<svg>`` root, so the model is asked
for a self-contained document holding ONLY the new elements. There is no
whole-document rewrite here, deliberately: the source view's Apply is the one
door for replacing a drawing, with its own undo-clearing rules — and the raw
editor keeps its own opt-out (``SvgFileDisplayView.steven_surface = ""``)
because sketch owns drawings.
"""

from toto.core.ai_surfaces import ELEMENT_ACTION, Action, AiSurface, registry

registry.register(AiSurface(
    key="sketch",
    label="Drawings",
    kind="code",
    file_type="svg",
    icon="fa-solid fa-pen-ruler",
    description="Generate new shapes on the board from an instruction.",
    actions=(
        Action(ELEMENT_ACTION, "Add to the drawing", "fa-solid fa-shapes",
               system=("You add new elements to an SVG drawing. Return ONLY a "
                       "complete, self-contained <svg> document containing "
                       "ONLY the NEW elements — never the existing drawing. "
                       "Use plain shapes: <path>, <rect>, <circle>, <ellipse>, "
                       "<line>, <polygon>, <polyline>, <text>. Give the root "
                       "the same viewBox as the current drawing. No scripts, "
                       "no event handlers, no animation, no external "
                       "references, no <!DOCTYPE>, no markdown fences, no "
                       "commentary."),
               needs_instruction=True,
               instruction_placeholder="a red arrow pointing at the box…",
               template=("{instruction}\n\nThe current drawing follows, as "
                         "context only. Return ONLY an <svg> holding the new "
                         "elements.\n\n{selection}")),
        Action("ask", "Ask about it…", "fa-solid fa-comment",
               system=("You answer questions about an SVG drawing. Answer in "
                       "prose, briefly, and only from what you were shown."),
               needs_instruction=True,
               instruction_placeholder="what is in this drawing?",
               template="{instruction}\n\nThe drawing:\n\n{selection}"),
    ),
))
