"""The portal's two doors onto the hosted forge.

``index`` is a page: the user's repositories, with links into Gitea itself. It
provisions the account on first visit, which is the whole reason it exists — a
user who has never had one otherwise discovers Gitea by being asked to sign in
to something they have no account on.

``remotes`` is JSON: the same list shaped as ``{label, url}`` for whatever wants
to offer a remote. ``toto.repo``'s init modal is the only caller today, and it
reads the payload without knowing what a Gitea is — see
``toto/repo/remotes.py`` for why the coupling runs this way round.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from toto.ui import PageProcessor

from . import client, permissions
from .errors import GiteaError
from .models import GiteaAccount


def _enabled() -> bool:
    return bool(getattr(settings, "GITEA_ENABLED", False))


@login_required
def index(request):
    if not permissions.can_use(request.user):
        raise PermissionDenied(permissions.refusal())

    base = getattr(settings, "GITEA_URL", "/gitea/").rstrip("/")
    repos, error = [], ""
    account = None
    if _enabled():
        # Provision on first visit. A failure here is shown, not raised: the
        # page's other half (the link into Gitea) is still useful, and a sidecar
        # that is starting up should not render as a 500.
        try:
            account = client.ensure_account(request.user)
            repos = [dict(r, browse_url=f"{base}/{r['owner']}/{r['name']}")
                     for r in client.list_repos(account)]
        except GiteaError as exc:
            error = str(exc)
        except Exception:
            error = "Gitea did not answer. It may still be starting up."

    context = {
        "gitea_enabled": _enabled(),
        "gitea_url": base + "/",
        "account": account,
        "repos": repos,
        "error": error,
    }
    return render(request, "gitea/index.html",
                  PageProcessor().decorate(context, request))


@login_required
@require_GET
def remotes(request):
    """``{"remotes": [{"label", "url"}]}`` — origins the caller may offer.

    An empty list for every reason there is (sidecar off, no account yet, Gitea
    unreachable, caller below the gate) and never an error status: the caller is
    a picker beside a text field that already works. Failing it would block
    making a local repository over a remote nobody asked for yet.
    """
    if not _enabled() or not permissions.can_use(request.user):
        return JsonResponse({"remotes": []})
    account = GiteaAccount.objects.filter(user=request.user).first()
    if account is None or not account.token_encrypted:
        return JsonResponse({"remotes": []})
    try:
        repos = client.list_repos(account)
    except Exception:
        return JsonResponse({"remotes": []})
    return JsonResponse({"remotes": [
        {"label": r["full_name"], "url": r["clone_url"]} for r in repos
    ]})
