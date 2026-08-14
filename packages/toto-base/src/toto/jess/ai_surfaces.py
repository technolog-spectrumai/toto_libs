"""What the assistant may do on the compose page.

Same wheel as the registry, but only ever imported by steven's autodiscovery
(``autodiscover_plugins("ai_surfaces")`` runs in StevenConfig.ready()), so a
host without the assistant — aurelian — never loads this module and the
compose page renders no button (``surface_for`` answers "").

One surface, BODY only. The handlers on the page target ``#id_body`` and
nothing else, because the contract "what ``source`` returns is what
``writeDocument`` replaces" must hold — a surface that read the body but
replaced the subject would be a trap. No ``file_type``: the body is plain
text, and screening plain text as HTML is the false-alarm trap
``editor/ai_surfaces.py`` documents. ``html_body`` is deliberately left
alone.

The element action is DECLARED (declared beats synthesised): the fragment
grammar is "paragraphs of email body text", not "a fragment of some file
type", and the draft wording keeps subject lines and headers out of a field
they would be pasted into.
"""

from toto.core.ai_surfaces import (ELEMENT_ACTION, Action, AiSurface,
                                   prose_actions, registry)

registry.register(AiSurface(
    key="jess-compose",
    label="Email",
    kind="prose",
    icon="fa-solid fa-envelope",
    description="Draft or rework the email you are composing.",
    actions=prose_actions() + (
        Action(ELEMENT_ACTION, "Draft the email", "fa-solid fa-plus",
               system=("You draft email body text. Return ONLY the text to "
                       "append to the email body — plain text paragraphs, no "
                       "subject line, no To/From headers, no signature "
                       "placeholders you were not asked for, no markdown "
                       "fences, no commentary."),
               needs_instruction=True,
               instruction_placeholder="invite the team to Friday's review…",
               template=("{instruction}\n\nThe email body so far follows, as "
                         "context only. Return ONLY the text to append."
                         "\n\n{selection}")),
    ),
))
