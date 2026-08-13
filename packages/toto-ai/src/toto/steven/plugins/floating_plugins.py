"""The side panel: ask about the whole document, from the corner of the page.

A `FloatingPlugin`, which is the platform's one global injection point — and the
reason four editors had to stop blanking `{% block floating_widgets %}` for this
to be possible. That blanking also silenced the gas pump, so the apps that
actually charge were the only ones never telling you what they cost.

**It hides itself when there is nothing to ask about.** Visibility is decided in
two places on purpose: here, whether the app is installed and somebody is logged
in; and in the browser, whether the page registered a `document` handler. The
server cannot know the second — a floating widget is rendered into every page,
and only the editor knows whether it has a document to hand over.
"""

from toto.core.plugin import FloatingPlugin


@FloatingPlugin.plugin(key="steven_drawer", order=10)
class StevenDrawerPlugin(FloatingPlugin):
    """A drawer beside the editor that answers questions about the document."""

    template_name = "steven/plugins/_drawer.html"

    def visible_for_request(self, request):
        user = getattr(request, "user", None)
        return bool(getattr(user, "is_authenticated", False))
