"""What the assistant may be asked about a file in the ACE editor.

**Four surfaces, not one**, because the editor opens eight file types and the
right question is not the same for all of them. The page picks by the file's
type, which it already knows.

`file_type` is set on exactly one of them, and the split is the reason there are
four rather than two. An answer bound for an `.html` file really is markup this
platform renders, so it is screened like an upload. An answer bound for a `.txt`
or a `.py` file is not: screening plain text as HTML would refuse a paragraph
that merely MENTIONS `<script>`, which is a false alarm that teaches people to
ignore the real one. Setting `file_type` where the scanner knows nothing about
the format would be a no-op dressed up as a guarantee.
"""

from toto.core.ai_surfaces import (
    AiSurface,
    code_actions,
    prose_actions,
    registry,
)

registry.register(AiSurface(
    key="editor-code",
    label="Code",
    kind="code",
    icon="fa-solid fa-code",
    description="Explain, comment or fix the code you selected.",
    actions=code_actions(),
))

registry.register(AiSurface(
    key="editor-latex",
    label="LaTeX",
    kind="latex",
    icon="fa-solid fa-superscript",
    description="Explain or rewrite the LaTeX you selected.",
    actions=code_actions("LaTeX"),
))

registry.register(AiSurface(
    key="editor-markup",
    label="HTML",
    kind="prose",
    file_type="html",
    icon="fa-solid fa-code",
    description="Rewrite, shorten, translate or explain the markup you selected.",
    actions=prose_actions(),
))

registry.register(AiSurface(
    key="editor-text",
    label="Text",
    kind="prose",
    icon="fa-solid fa-align-left",
    description="Rewrite, shorten, translate or explain the text you selected.",
    actions=prose_actions(),
))
