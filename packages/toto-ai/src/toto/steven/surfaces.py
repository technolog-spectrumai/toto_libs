"""Backwards-compatible alias. The registry lives in toto-base.

``toto.core.ai_surfaces`` holds ``AiSurface``, ``Action``, the registry and the
shared action vocabulary, because the apps that DECLARE surfaces cannot depend
on this wheel. Re-exported here so ``toto.steven`` code reads naturally and so a
future consumer has one obvious place to look.
"""

from toto.core.ai_surfaces import (  # noqa: F401
    Action,
    AgentVoice,
    AiSurface,
    DuplicateSurface,
    SurfaceRegistry,
    DOCUMENT_ACTION,
    LANGUAGE_NAMES,
    build_messages,
    code_actions,
    compose_system,
    document_action,
    prose_actions,
    registry,
    resolve_action,
)
