"""Is the writer a place users go, or only an engine other apps drive?

Cyprian used to be optional: a host either installed it or did not, and
``BUILD_CYPRIAN`` answered both questions at once — "is the code here" and "is
Documents a feature". Those came apart when kanban's project wikis started being
written through this editor. A host that runs the boards needs the writer
present whether or not it wants a Documents tile, so the install stopped being a
choice and the *visibility* became one.

``SHOW_DOCUMENT_EDITOR`` is that second question, and only that one. Off, the
library index 404s and the vault stops offering Edit/Play on a document, so
nothing leads a user to the writer on its own. Everything an owning app needs —
``cyprian:edit``, ``cyprian:save``, the media endpoints — stays mounted and
ungated, because a wiki page must open regardless.

Defaults to ON, and defaults ON for a host that has never heard of the flag:
delta installs this app and has no such setting, and the honest answer there is
the behaviour it already had.
"""

from django.conf import settings


def document_editor_shown() -> bool:
    return bool(getattr(settings, "SHOW_DOCUMENT_EDITOR", True))
