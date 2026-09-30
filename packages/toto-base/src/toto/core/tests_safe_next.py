"""`next` stays on this site (2026-09-30, stage 31.11).

`toto.core.safe_next` on its own, and the two password doors that follow it:
`core:login` / `sso:login` (password_login_view) and the sign-out
(password_logout_view). Until now both followed `?next=` wherever it pointed,
so a mail could send a member through this site's genuine sign-in page and on
to a copy of it."""

from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from toto.core.auth_views import password_login_view, password_logout_view
from toto.core.models import Platform
from toto.core.safe_next import safe_next

User = get_user_model()
EVIL = "https://evil.example.com/phish"


class SafeNextTests(SimpleTestCase):
    def request(self, secure=False):
        return RequestFactory().get("/", secure=secure, HTTP_HOST="zenobia.example.org")

    def test_a_path_on_this_site_is_kept(self):
        self.assertEqual(safe_next(self.request(), "/wiki/?page=2"), "/wiki/?page=2")

    def test_an_absolute_url_naming_this_host_is_kept(self):
        url = "http://zenobia.example.org/vault/"
        self.assertEqual(safe_next(self.request(), url), url)

    def test_anywhere_else_answers_the_fallback(self):
        for candidate in (EVIL, "//evil.example.com/", "/\\evil.example.com/",
                          "https:evil.example.com", "javascript:alert(1)",
                          "http://zenobia.example.org.evil.example.com/"):
            self.assertEqual(safe_next(self.request(), candidate, "/home/"), "/home/",
                             candidate)

    def test_nothing_answers_the_fallback(self):
        for candidate in (None, "", "   "):
            self.assertEqual(safe_next(self.request(), candidate, "/home/"), "/home/")
        self.assertEqual(safe_next(self.request(), None), "")

    def test_a_secure_request_is_never_sent_back_to_plain_http(self):
        request = self.request(secure=True)
        self.assertEqual(safe_next(request, "http://zenobia.example.org/vault/", "/"), "/")
        self.assertEqual(safe_next(request, "https://zenobia.example.org/vault/", "/"),
                         "https://zenobia.example.org/vault/")


def _request(method="get", data=None, *, user=None):
    request = getattr(RequestFactory(), method)("/core/login/", data or {})
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = user or AnonymousUser()
    request._messages = FallbackStorage(request)
    return request


def _door(request):
    return password_login_view(request, template_name="oya/login.html", page_title="Login")


@override_settings(LOGIN_RETRY_COOLDOWN_SECONDS=0)
class PasswordDoorsStayHereTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test", author="Tests", publication_year=2026,
                                active=True)
        cls.ada = User.objects.create_user("ada", password="Correct-horse-9")

    def test_signing_in_with_an_off_site_next_lands_on_the_dashboard(self):
        request = _request("post", {"username": "ada", "password": "Correct-horse-9",
                                    "next": EVIL})
        response = _door(request)
        self.assertEqual((response.status_code, response["Location"]),
                         (302, reverse("core:dashboard")))
        # The member is still signed in; only the destination was refused.
        self.assertEqual(request.session[SESSION_KEY], str(self.ada.pk))

    def test_an_off_site_next_in_the_query_is_refused_on_a_post_too(self):
        request = RequestFactory().post(f"/core/login/?next={EVIL}",
                                        {"username": "ada", "password": "Correct-horse-9"})
        SessionMiddleware(lambda r: None).process_request(request)
        request.user = AnonymousUser()
        request._messages = FallbackStorage(request)
        self.assertEqual(_door(request)["Location"], reverse("core:dashboard"))

    def test_a_signed_in_visitor_is_not_forwarded_off_site(self):
        response = _door(_request(data={"next": "//evil.example.com/"}, user=self.ada))
        self.assertEqual(response["Location"], reverse("core:dashboard"))

    def test_the_form_does_not_carry_a_refused_next(self):
        response = _door(_request(data={"next": EVIL}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "evil.example.com")

    def test_the_form_still_carries_a_local_next(self):
        response = _door(_request(data={"next": "/wiki/"}))
        self.assertContains(response, 'value="/wiki/"')

    def test_signing_out_with_an_off_site_next_lands_on_the_dashboard(self):
        response = password_logout_view(_request(data={"next": EVIL}, user=self.ada))
        self.assertEqual(response["Location"], reverse("core:dashboard"))

    def test_signing_out_keeps_an_absolute_next_on_this_host(self):
        response = password_logout_view(_request(data={"next": "http://testserver/welcome/"}))
        self.assertEqual(response["Location"], "http://testserver/welcome/")
