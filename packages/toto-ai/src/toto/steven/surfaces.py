"""Backwards-compatible alias, plus steven's own surface. The registry lives in toto-base.

``toto.core.ai_surfaces`` holds ``AiSurface``, ``Action``, the registry and the
shared action vocabulary, because the apps that DECLARE surfaces cannot depend
on this wheel. Re-exported here so ``toto.steven`` code reads naturally and so a
future consumer has one obvious place to look.

The **chat surface** is registered here rather than in an ``ai_surfaces.py``:
autodiscovery is for OTHER apps declaring surfaces; the chat chip is steven's
own feature, and this module is imported by ``StevenConfig.ready()`` on exactly
the hosts that install steven. Import-time registration is the established
pattern (toto.core registers ``file`` the same way), and re-registering an
equal value is a no-op, so a double import is safe.
"""

from toto.core.ai_surfaces import (  # noqa: F401
    Action,
    AgentVoice,
    AiSurface,
    DuplicateSurface,
    SurfaceRegistry,
    DOCUMENT_ACTION,
    ELEMENT_ACTION,
    LANGUAGE_NAMES,
    build_messages,
    code_actions,
    compose_system,
    document_action,
    element_action,
    prose_actions,
    registry,
    resolve_action,
)

# ---------------------------------------------------------------------------
# The chat surface — what the floating chip talks to
# ---------------------------------------------------------------------------
# No kind and no file_type on purpose: no kind-note applies (chat is not one
# editor's dialect), and a chat answer is something to READ, never something
# applied into a document, so there is nothing for the scanner to screen.
#
# History policy: STRICTLY single-turn. The transcript in the chip is Alpine
# state, visual only; every POST carries the current message and nothing else.
# ``build_messages`` refuses history by design — the parked app's unbounded
# resend grew the bill quadratically — and an ephemeral chat is the one place
# that rule costs nothing to keep.

registry.register(AiSurface(
    key="chat",
    label="Chat",
    kind="",
    icon="fa-solid fa-comment-dots",
    description="The floating chat — quick questions, anywhere.",
    actions=(
        # The message IS the selection: ask() refuses an empty selection and
        # caps it at MAX_SELECTION, which is exactly the contract a chat
        # message wants.
        Action("say", "Chat", "fa-solid fa-comment-dots",
               system=("You are chatting with a user of this platform. Answer "
                       "in prose, briefly and directly. You are shown only the "
                       "current message — earlier messages are not available, "
                       "so never refer to them."),
               template="{selection}"),
        # Named "ask" deliberately: every vocabulary carries "ask", and the
        # registry sweep in tests counts on it. This is the chip's document
        # mode — the page's document as selection, the question as instruction.
        Action("ask", "Ask about it…", "fa-solid fa-comment",
               system=("You answer questions about a document. Answer in "
                       "prose, briefly, and only from what you were shown."),
               needs_instruction=True,
               instruction_placeholder="what is this about?",
               template="{instruction}\n\nThe document:\n\n{selection}"),
    ),
))
