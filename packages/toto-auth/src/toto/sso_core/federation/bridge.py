"""In-process OIDC transport between a consumer and a provider.

Production separates the two sides by network. Here they are separated by
URLCONF: :func:`provider_urlconf` swaps ``settings.ROOT_URLCONF`` for the
provider's tree for exactly one hop.

That is what resolves the namespace clash. Both urlconfs declare
``app_name = "sso"``, and *both sides* hard-reverse ``sso:`` names —
``sso_client.views`` reverses ``sso:callback``, ``sso_master.views`` reverses
``sso:token`` and ``sso:consent``. Mounting them side by side would break
whichever one lost the ``sso`` instance namespace. Never having them live at the
same moment costs nothing and keeps each side seeing exactly the tree it sees in
production.

``override_settings`` is what makes the swap safe: Django's
``root_urlconf_changed`` receiver fires on both enter and exit and calls
``clear_url_caches()`` plus ``set_urlconf(None)``, so a ``reverse()`` after the
hop lands back on the consumer's tree with no manual bookkeeping.

One known limit: ``sso_client.urls`` evaluates ``is_installed("toto.social_login")``
at *module import* and conditionally appends a route. Swapping ROOT_URLCONF does
not re-import a cached module, so that branch is frozen at whatever it was on
first import. Harmless with social_login installed (as it is here), but it means
this harness cannot exercise a social-login-less consumer, and any future
conditional in either urls module will be pinned the same way.
"""
import json
from contextlib import contextmanager
from unittest import mock
from urllib.parse import parse_qsl, urlparse

from django.test import Client, override_settings
from django.urls import reverse

PROVIDER_URLCONF = "toto.sso_core.federation.provider_urls"


@contextmanager
def provider_urlconf():
    """Resolve and reverse against the provider's url tree for the duration."""
    with override_settings(ROOT_URLCONF=PROVIDER_URLCONF):
        yield


class LoopbackResponse:
    """Exactly the surface toto.sso_client touches on a requests.Response.

    ``.ok``, ``.status_code``, ``.json()``, ``.text`` — and deliberately nothing
    else. If the consumer ever reaches for ``.content`` or
    ``.raise_for_status()``, this should fail loudly rather than quietly diverge
    from what ``requests`` would have done.
    """

    def __init__(self, response):
        self._response = response
        self.status_code = response.status_code

    @property
    def ok(self):
        return self.status_code < 400

    @property
    def text(self):
        return self._response.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


class ProviderLoopback:
    """Dispatch the consumer's server-side calls into the provider's views.

    Only the back-channel hops (token, userinfo, jwks) come through here. The
    browser hops are driven by the test itself, so the provider's session cookie
    is a real one.
    """

    def __init__(self, portal_url):
        self.portal_url = portal_url.rstrip("/")
        self.host = urlparse(self.portal_url).netloc
        self.client = Client(headers={"host": self.host})
        self.calls = []          # [(method, path, payload)] — assertable
        self._hooks = {}         # path -> callable, run just before dispatch

    def before(self, path, hook):
        """Run ``hook()`` immediately before the provider serves ``path``.

        Models the races the real world has: an account disabled between the
        token exchange and the userinfo read, say — which is the only channel
        that can carry a deactivation, since an inactive user can never reach
        /authorize in the first place.
        """
        self._hooks[path] = hook

    def _split(self, url):
        if not url.startswith(self.portal_url + "/"):
            raise AssertionError(
                f"the consumer called {url!r}, which is not on its configured "
                f"portal {self.portal_url!r} — a hardcoded host leaked in"
            )
        return url[len(self.portal_url):]

    def _dispatch(self, method, path, payload, headers, *, content_type=None):
        self.calls.append((method, path, payload if isinstance(payload, str) else dict(payload or {})))
        hook = self._hooks.pop(path, None)
        if hook:
            hook()
        with provider_urlconf():
            fn = getattr(self.client, method.lower())
            kwargs = {"headers": headers or {}}
            if content_type:
                kwargs["content_type"] = content_type
            # secure=True because the real back-channel is https:// — the consumer
            # will not call a provider over plaintext, and the provider refuses it.
            # Without this the harness cannot reach any view that checks
            # request.is_secure(), which /sso/enroll/ does.
            return LoopbackResponse(fn(path, payload or {}, secure=True, **kwargs))

    def post(self, url, data=None, json=None, headers=None, timeout=None, **_):
        """``requests.post`` accepts either ``data=`` or ``json=``.

        Both are used in this codebase — the token exchange posts form data, the
        pairing call posts JSON — so the loopback has to honour the distinction or
        one of them silently arrives with an empty body.
        """
        if json is not None:
            import json as _json

            return self._dispatch(
                "POST", self._split(url), _json.dumps(json), headers,
                content_type="application/json",
            )
        return self._dispatch("POST", self._split(url), data, headers)

    def get(self, url, params=None, headers=None, timeout=None, **_):
        return self._dispatch("GET", self._split(url), params, headers)

    @contextmanager
    def patched(self):
        """Route the consumer's outbound HTTP here.

        Patches attributes of the ``requests`` module object that
        ``toto.sso_client.views`` imported, the same house pattern as
        ``toto/social_login/tests/test_social_flow.py``.
        """
        with mock.patch("toto.sso_client.views.http_requests.post", self.post), \
             mock.patch("toto.sso_client.views.http_requests.get", self.get):
            yield


class FederationBrowser:
    """Two cookie jars, and the redirects a real browser performs between them.

    consumer ``/sso/login/`` → provider ``/sso/authorize/`` → consumer
    ``/sso/callback/``.
    """

    def __init__(self, *, portal_url, consumer_host="consumer.test"):
        self.loopback = ProviderLoopback(portal_url)
        self.provider = self.loopback.client
        self.consumer = Client(headers={"host": consumer_host})

    def sign_in_at_provider(self, user):
        with provider_urlconf():
            self.provider.force_login(user)

    def start_login(self, next_url=""):
        with self.consumer_urlconf():
            return self.consumer.get(
                reverse("sso:login"), {"next": next_url} if next_url else {}
            )

    @contextmanager
    def consumer_urlconf(self):
        # The consumer tree is ROOT_URLCONF already; this exists so a test that
        # nests calls inside provider_urlconf() still reverses on the right one.
        from django.conf import settings
        with override_settings(
            ROOT_URLCONF="toto.sso_core.federation.consumer_urls"
        ):
            yield settings

    def _portal_path(self, location):
        if location.startswith(self.loopback.portal_url):
            return location[len(self.loopback.portal_url):]
        return location

    def authorize(self, location, *, approve_consent=False):
        """Follow the redirect to the provider's authorize endpoint.

        A trusted client gets a 302 straight back to the consumer. An untrusted
        one gets the consent page (200) instead, and approving it is one more
        hop: the consent POST itself issues the code and redirects back to the
        consumer (2026-10-02; it used to redirect to /sso/authorize/ with
        consent=approved, a GET that any page could make).
        """
        with provider_urlconf():
            response = self.provider.get(self._portal_path(location))
            if approve_consent and response.status_code == 200:
                response = self.provider.post(
                    reverse("sso:consent"),
                    {
                        "decision": "approve",
                        "query_string": response.context["query_string"],
                    },
                )
        return response

    def callback(self, location):
        """Hand the provider's redirect back to the consumer."""
        parsed = urlparse(location)
        with self.consumer_urlconf(), self.loopback.patched():
            return self.consumer.get(parsed.path, dict(parse_qsl(parsed.query)))

    def login(self, user, *, next_url="", approve_consent=False):
        """The whole dance; returns the consumer's final response."""
        self.sign_in_at_provider(user)
        started = self.start_login(next_url)
        authorized = self.authorize(started["Location"], approve_consent=approve_consent)
        return self.callback(authorized["Location"])
