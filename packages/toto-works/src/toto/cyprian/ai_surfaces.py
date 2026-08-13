"""What the assistant may be asked about a document.

Prose actions, and `file_type="html"` — a cyprian document's body IS HTML, so
anything the model returns is bound for a rendered page and goes through
`toto.vault.scanning` before it is offered. That is the same judgement an upload
gets, for the same reason: a completion is untrusted third-party content.
"""

from toto.core.ai_surfaces import AiSurface, prose_actions, registry

registry.register(AiSurface(
    key="cyprian",
    label="Documents",
    kind="prose",
    file_type="html",
    icon="fa-solid fa-file-lines",
    description="Rewrite, shorten, translate or explain the text you selected.",
    actions=prose_actions(),
))
