"""What the assistant may be asked about a slide's prose.

`file_type="html"` because a memo block's payload IS HTML — the same reason
cyprian's surface is screened. What differs is the UNIT: memo's blocks are
already addressable (each carries its own `payload`, `id` and `slot`), so the
natural thing to act on is a block, and the client offers the whole block when
nothing narrower is selected.
"""

from toto.core.ai_surfaces import AiSurface, prose_actions, registry

registry.register(AiSurface(
    key="memo",
    label="Presentations",
    kind="prose",
    file_type="html",
    icon="fa-solid fa-display",
    description="Rewrite, shorten, translate or explain the text in a block.",
    actions=prose_actions(),
))
