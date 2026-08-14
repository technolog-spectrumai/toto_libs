"""The chat chip: ask the assistant from the corner of any page.

A `FloatingPlugin`, which is the platform's one global injection point — and the
reason four editors had to stop blanking `{% block floating_widgets %}` for this
to be possible. That blanking also silenced the gas pump, so the apps that
actually charge were the only ones never telling you what they cost.

The drawer this grew from showed itself only on editor pages; the chat chip
shows for every authenticated user everywhere — a quick question does not need
a document. When the page DOES have one (an editor registered a ``document``
handler), the chip offers to include it, and the old drawer's whole feature
survives as that toggle.

Conversations are EPHEMERAL: the transcript is Alpine state in the browser,
and each question is a single-turn run — nothing is stored but the usage
records every run already writes.
"""

from toto.core.plugin import FloatingPlugin


@FloatingPlugin.plugin(key="steven_chat", order=10)
class StevenChatPlugin(FloatingPlugin):
    """A floating chat modal, on every page the platform renders."""

    template_name = "steven/plugins/_chat.html"

    def visible_for_request(self, request):
        user = getattr(request, "user", None)
        return bool(getattr(user, "is_authenticated", False))
