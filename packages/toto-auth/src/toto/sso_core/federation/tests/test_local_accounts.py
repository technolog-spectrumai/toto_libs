"""A consumer host that has accounts of its own, not only the provider's.

Three things had to be true before a host could have both, and none of them were:

* **``sso:login`` had to stop being a redirect.** ``LOGIN_URL`` is ``"sso:login"``
  in every auth mode, so it is where ``@login_required`` sends everyone — and on a
  consumer it bounced straight to the provider. A local-only user was therefore
  sent to a portal that has never heard of them, and got a 400 for it. There was
  no page on which they could type a password.
* **Password reset had to exist here.** All four views shipped in ``sso_master``,
  so on a consumer ``sso:password_reset`` did not reverse and the link hid itself.
  Correct when every account belonged to the provider; useless once they do not.
* **A claim had to stop being able to adopt a local account.** That is the subject
  of test_claims_mapping.py; this file covers the surfaces built on top of it,
  including the deliberate ``/sso/link/`` that replaced matching by email.

None of it is a fourth auth mode: ``login_url()`` and ``authentication_backends()``
are still identical across the three, which test_auth_config.py asserts.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.sso_client.models import FederatedIdentity

from ..bridge import FederationBrowser, provider_urlconf
from ..fixtures import federation_fixture

User = get_user_model()

PORTAL = "http://provider.test"
REDIRECT = "http://consumer.test/sso/callback/"

CONSUMER_URLS = "toto.sso_core.federation.consumer_urls"


@override_settings(ROOT_URLCONF=CONSUMER_URLS)
class HybridLoginPageTests(TestCase):
    """What ``sso:login`` serves on a host that has local users."""

    @classmethod
    def setUpTestData(cls):
        federation_fixture(portal_url=PORTAL, redirect_uri=REDIRECT)

    def test_without_the_flag_the_login_url_still_redirects_to_the_provider(self):
        # A pure consumer must be completely unaffected by this feature.
        response = self.client.get(reverse("sso:login"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(PORTAL, response["Location"])

    @override_settings(TOTO_SSO_LOCAL_LOGIN=True)
    def test_with_the_flag_it_renders_a_page_offering_both(self):
        response = self.client.get(reverse("sso:login"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The federated route, as a button rather than a redirect...
        self.assertIn(reverse("sso:federated_login"), body)
        # ...and a password form that posts back here, not to core:login, because
        # this is the view that knows about the button.
        self.assertIn(f'action="{reverse("sso:login")}"', body)
        self.assertIn('name="password"', body)

    @override_settings(TOTO_SSO_LOCAL_LOGIN=True)
    def test_the_button_names_the_provider(self):
        # OIDCProviderConfig.label exists to be displayed, and get_config() did not
        # pass it through — so the button read "Sign in with the portal" on every
        # consumer host, which is the fallback, not the intent.
        # Asserted on the BUTTON TEXT, not merely on the label appearing somewhere:
        # the fixture also names the Platform "Federation suite", so a looser check
        # passes on the site name in the header and proves nothing.
        response = self.client.get(reverse("sso:login"))
        body = response.content.decode()
        self.assertIn("Sign in with Federation suite", body)
        self.assertNotIn("Sign in with the portal", body)

    @override_settings(TOTO_SSO_LOCAL_LOGIN=True)
    def test_a_local_user_signs_in_on_that_page(self):
        User.objects.create_user("localonly", "l@example.org", "pw")
        response = self.client.post(
            reverse("sso:login"), {"username": "localonly", "password": "pw"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    @override_settings(TOTO_SSO_LOCAL_LOGIN=True)
    def test_a_bad_password_re_renders_rather_than_bouncing_to_the_provider(self):
        User.objects.create_user("localonly", "l@example.org", "pw")
        response = self.client.post(
            reverse("sso:login"), {"username": "localonly", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(TOTO_SSO_LOCAL_LOGIN=True)
    def test_where_login_required_sends_people_is_a_usable_page(self):
        # The concrete bug: @login_required sends everyone to LOGIN_URL, which on
        # a consumer redirected to the provider, which had never heard of a
        # studio-only user and answered 400. Asserted through the name every host
        # is told to use rather than a protected view, because which views are
        # protected is a host's decision and this harness installs almost none.
        from toto.auth_config import login_url, resolve_auth

        cfg = resolve_auth({"TOTO_AUTH_MODE": "consumer"}.get)
        name = login_url(cfg)
        self.assertEqual(name, "sso:login")

        landed = self.client.get(reverse(name))
        self.assertEqual(landed.status_code, 200)
        self.assertIn('name="password"', landed.content.decode())

    @override_settings(TOTO_SSO_LOCAL_LOGIN=True)
    def test_the_next_url_survives_the_federated_button(self):
        response = self.client.get(reverse("sso:login"), {"next": "/vault/"})
        self.assertIn("next=/vault/", response.content.decode())


@override_settings(ROOT_URLCONF=CONSUMER_URLS)
class ConsumerPasswordResetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        federation_fixture(portal_url=PORTAL, redirect_uri=REDIRECT)

    def test_the_four_reset_routes_reverse_on_a_consumer(self):
        # They only existed in sso_master before, so every one of these raised
        # NoReverseMatch on a consumer host.
        for name in ("password_reset", "password_reset_done", "password_reset_complete"):
            with self.subTest(name=name):
                self.assertTrue(reverse(f"sso:{name}"))
        self.assertTrue(reverse("sso:password_reset_confirm", args=["uid", "tok"]))

    def test_the_provider_serves_the_same_names(self):
        # One definition, two mounts, at different depths — sso_master.urls is
        # included at "" and carries its own sso/ segment, sso_client.urls at
        # "sso/". The names must not drift between them.
        with provider_urlconf():
            self.assertTrue(reverse("sso:password_reset"))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_a_non_delivering_backend_declines_rather_than_dropping_mail(self):
        # console/dummy print or discard; accepting a reset request against one
        # tells the user mail is coming when it never is.
        response = self.client.get(reverse("sso:password_reset"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("sso:login"), response["Location"])

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST="localhost", DEFAULT_FROM_EMAIL="noreply@example.org",
    )
    def test_a_federated_account_cannot_be_reset_but_a_local_one_can(self):
        from django.contrib.auth.forms import PasswordResetForm

        local = User.objects.create_user("localonly", "local@example.org", "pw")
        federated = User.objects.create_user("oidc_sub-1", "fed@example.org")
        federated.set_unusable_password()
        federated.save(update_fields=["password"])

        # Django's own get_users() filters on has_usable_password(), so this
        # property needs no code of ours — but it does need asserting, because it
        # is what stops a reset email becoming a way into a provider-owned account.
        self.assertEqual(
            [u.pk for u in PasswordResetForm().get_users("local@example.org")], [local.pk],
        )
        self.assertEqual(list(PasswordResetForm().get_users("fed@example.org")), [])


@override_settings(ROOT_URLCONF=CONSUMER_URLS)
class DeliberateLinkingTests(TestCase):
    """``/sso/link/`` — the replacement for matching an incoming identity by email.

    Only the account's own authenticated session can start it. That is the whole
    security argument: a provider cannot claim a local account, and a local user
    cannot claim someone else's provider identity.
    """

    @classmethod
    def setUpTestData(cls):
        federation_fixture(portal_url=PORTAL, redirect_uri=REDIRECT)

    def setUp(self):
        self.local = User.objects.create_user("ada", "ada@example.org", "pw")

    def test_linking_requires_being_signed_in(self):
        response = self.client.get(reverse("sso:federated_link"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(PORTAL, response["Location"])   # to our login, not the portal

    def test_a_signed_in_user_starts_the_round_trip_with_a_link_marker(self):
        self.client.force_login(self.local)
        response = self.client.get(reverse("sso:federated_link"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(PORTAL, response["Location"])
        self.assertIn("oidc_link", response.cookies)

    def test_completing_a_link_records_the_identity_without_touching_the_account(self):
        browser = FederationBrowser(portal_url=PORTAL)
        browser.sign_in_at_provider(self.local)
        with browser.consumer_urlconf():
            browser.consumer.force_login(self.local)
            started = browser.consumer.get(reverse("sso:federated_link"))
        authorized = browser.authorize(started["Location"])
        response = browser.callback(authorized["Location"])

        self.assertEqual(response.status_code, 302)
        identity = FederatedIdentity.objects.get(user=self.local)
        # provisioned=False: an existing local account was linked, not created for
        # the provider. A later audit needs to tell those apart.
        self.assertFalse(identity.provisioned)

        self.local.refresh_from_db()
        self.assertTrue(self.local.check_password("pw"))   # password survives
        self.assertFalse(self.local.is_staff)              # no privilege sync

    def test_a_subject_already_held_by_someone_else_is_refused(self):
        from toto.sso_master.services import get_subject_for_user
        from toto.sso_client.models import OIDCProviderConfig

        other = User.objects.create_user("bob", "bob@example.org", "pw")
        provider = OIDCProviderConfig.objects.filter(active=True).first()
        # bob already answers to ada's provider subject.
        FederatedIdentity.objects.create(
            provider=provider, sub=get_subject_for_user(self.local), user=other,
        )

        browser = FederationBrowser(portal_url=PORTAL)
        browser.sign_in_at_provider(self.local)
        with browser.consumer_urlconf():
            browser.consumer.force_login(self.local)
            started = browser.consumer.get(reverse("sso:federated_link"))
        authorized = browser.authorize(started["Location"])
        response = browser.callback(authorized["Location"])

        self.assertEqual(response.status_code, 302)
        # Refused, not reassigned: moving it would hand ada bob's federated route.
        self.assertEqual(FederatedIdentity.objects.count(), 1)
        self.assertEqual(FederatedIdentity.objects.get().user, other)

    def test_linking_twice_is_idempotent(self):
        for _ in range(2):
            browser = FederationBrowser(portal_url=PORTAL)
            browser.sign_in_at_provider(self.local)
            with browser.consumer_urlconf():
                browser.consumer.force_login(self.local)
                started = browser.consumer.get(reverse("sso:federated_link"))
            authorized = browser.authorize(started["Location"])
            browser.callback(authorized["Location"])
        self.assertEqual(FederatedIdentity.objects.filter(user=self.local).count(), 1)
