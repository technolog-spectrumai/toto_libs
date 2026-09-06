"""lakeFS's login form, answered by the portal.

WHAT THIS IS. lakeFS Community has exactly one user — the admin created at
setup — and SSO/OIDC is "available in lakeFS Enterprise". The one external
hook the open-source build supports is the *remote authenticator*: when
somebody submits the lakeFS login form, lakeFS POSTs `{"username",
"password"}` to a URL and logs them in if the reply is 2xx with a non-empty
`external_user_identifier` naming a user that exists. Its own documentation
states the limit plainly: "when using Remote Authenticator with OSS,
authentication is limited to this admin user."

So this view is that URL. It checks the pair against Django's own
`authenticate()`, admits only an active SUPERUSER, and answers with the name
the single lakeFS user was created under — `LAKEFS_ADMIN_USERNAME`, which
deploy.py sets to the platform's ADMIN_USERNAME for exactly this reason. The
admin enters lakeFS with their portal password; nobody else enters at all.

That is not a workaround for missing SSO. It is the store's access model on
this host, stated in one place: the platform is the store's sole writer
(the Celery worker holds the key pair and ingests on the Lodge's behalf),
and who may put what where is decided by lacedo's permissions, never by
lakeFS's. Verified end to end against the real image before this was
written: right password → 200 and a session identifying as the admin; wrong
password → 401; the key pair unaffected.

WHO MAY CALL IT. lakeFS, over the compose network — `http://web-<name>:8000/`
— which never passes through nginx. From the public side it has no caller,
so it gets none, twice over: deploy.py emits an exact-match `deny all` for
this path in nginx, and the view refuses a request whose Host is the public
name. Two layers, each testable on its own, because a username+password
oracle reachable from the internet with only a rate limit in front of it is
a brute-force surface, however unlikely the credential.

THE CONTRACT lakeFS EXPECTS, and why every branch returns JSON with that one
key: a failure is `<200 or >300` with `{"external_user_identifier": ""}`.
A 400 for a malformed body and a 403 for the wrong door both satisfy it —
lakeFS shows "the credentials don't match" and moves on. Never a 500: a
crash in here would read, from the login form, exactly like a wrong password.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import authenticate
from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

log = logging.getLogger(__name__)


def _refused(status: int) -> JsonResponse:
    return JsonResponse({"external_user_identifier": ""}, status=status)


def _arrived_by_the_public_name(request) -> bool:
    """True when the request came in through the platform's public host.

    `HTTP_HOST` is read raw rather than through `get_host()`: this is a
    comparison, not a trust decision, and `get_host()` raises DisallowedHost
    for a name outside ALLOWED_HOSTS — turning a refusal into a 400 with a
    traceback in the log for what is, from here, just a wrong door.
    """
    from .services import get_public_base_url

    public = (urlsplit(get_public_base_url()).hostname or "").lower()
    if not public:
        return False
    host = (request.META.get("HTTP_HOST") or "").split(":", 1)[0].lower()
    return host == public


@csrf_exempt
@require_POST
def lakefs_authenticator(request):
    if not getattr(settings, "LAKEFS_ENABLED", False):
        # No store on this host means no login form to answer for. 404, not
        # 403: the route's existence is not information worth handing out.
        raise Http404
    if _arrived_by_the_public_name(request):
        return _refused(403)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _refused(400)
    if not isinstance(body, dict):
        return _refused(400)
    username = str(body.get("username") or "")
    password = str(body.get("password") or "")
    if not username or not password:
        return _refused(401)

    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active or not user.is_superuser:
        # A staff member who is not a superuser is refused too: whoever gets
        # in becomes the store's ONE user, which is its administrator.
        return _refused(401)

    identifier = (getattr(settings, "LAKEFS_ADMIN_USERNAME", "") or "").strip()
    if not identifier:
        log.error("lakefs_authenticator: LAKEFS_ADMIN_USERNAME is empty — "
                  "nothing to map a superuser onto")
        return _refused(401)
    return JsonResponse({"external_user_identifier": identifier})
