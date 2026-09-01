"""301s for the pre-split `/ambrosia/...` addresses.

Old bookmarks, the vault Edit button's historical links and anything else that
learned the old prefix land here and are sent to whichever language app owns
the workspace's kind. Query strings survive the redirect.
"""

from django.http import Http404, HttpResponsePermanentRedirect
from django.urls import path, re_path, reverse

from . import registry
from .models import Workspace


def _redirect(request, target):
    query = request.META.get("QUERY_STRING", "")
    return HttpResponsePermanentRedirect(target + (f"?{query}" if query else ""))


def _lobby(request):
    # From the registry, not a literal list: the language apps register
    # themselves at ready(), and a hardcoded pair silently stops redirecting
    # the moment one of them is renamed or a third arrives.
    from . import registry as workspace_registry

    for namespace in sorted(app.namespace for app in workspace_registry.all_apps()):
        if registry.for_namespace(namespace):
            return _redirect(request, reverse(f"{namespace}:lobby"))
    raise Http404("No workspace apps are installed.")


def _room(request, slug, rest=""):
    workspace = Workspace.objects.filter(slug=slug).only("kind").first()
    if workspace is None:
        raise Http404("No such workspace")
    app = registry.for_kind(workspace.kind)
    if app is None:
        raise Http404("No such workspace")
    base = reverse(f"{app.namespace}:workspace", args=[slug])
    return _redirect(request, base.rstrip("/") + "/" + (rest or "").lstrip("/"))


urlpatterns = [
    path("", _lobby),
    re_path(r"^w/(?P<slug>[-\w]+)/(?P<rest>.*)$", _room),
    re_path(r"^w/(?P<slug>[-\w]+)$", _room),
    re_path(r"^.*$", _lobby),
]
