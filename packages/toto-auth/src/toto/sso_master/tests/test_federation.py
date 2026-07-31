"""The federation staff console and the patron-only platform-info API.

Runs under the provider-shaped tree (`toto.sso_master.testing.settings`), where the
`sso` namespace resolves to sso_master, so `sso:platform_info` / `sso:federation_console`
reverse directly.
"""
import base64

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.sso_master.models import SSORelyingParty

User = get_user_model()


class PlatformInfoApiTests(TestCase):
    def setUp(self):
        self.platform = Platform.objects.create(
            site_name="Zenobia", domain="zenobia.test", author="Us",
            publication_year=2026, active=True,
        )
        self.party = SSORelyingParty.objects.create(
            name="Studio", client_id="studio", client_type=SSORelyingParty.CONFIDENTIAL,
            allowed_scopes="openid email profile", active=True, pairing_managed=True,
        )
        self.secret = self.party.rotate_client_secret()
        self.party.save()          # rotate_client_secret sets the hash; the caller persists it
        self.url = reverse("sso:platform_info")

    def _basic(self, cid, secret):
        raw = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        return self.client.get(self.url, HTTP_AUTHORIZATION=f"Basic {raw}")

    def test_a_patron_gets_the_platform_identity(self):
        res = self._basic("studio", self.secret)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["site_name"], "Zenobia")
        self.assertEqual(body["domain"], "zenobia.test")

    def test_credentials_work_in_the_post_body_too(self):
        res = self.client.post(self.url, {"client_id": "studio", "client_secret": self.secret})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["site_name"], "Zenobia")

    def test_the_logo_is_exposed_as_an_absolute_url(self):
        # Set the stored name only — never write a file. `.url` is built from the name
        # + MEDIA_URL, and writing into the source tree's MEDIA_ROOT would leave a
        # gitignored artifact (see test_no_source_file_under_packages_is_gitignored).
        self.platform.logo = "federation_logos/logo.png"
        self.platform.save(update_fields=["logo"])
        body = self._basic("studio", self.secret).json()
        self.assertTrue(body["logo_url"].startswith("http"))
        self.assertIn("federation_logos/logo.png", body["logo_url"])

    def test_no_logo_is_null_not_an_error(self):
        body = self._basic("studio", self.secret).json()
        self.assertIsNone(body["logo_url"])

    def test_a_wrong_secret_is_refused(self):
        self.assertEqual(self._basic("studio", "WRONG").status_code, 401)

    def test_an_unknown_client_is_refused(self):
        self.assertEqual(self._basic("nobody", self.secret).status_code, 401)

    def test_an_inactive_relying_party_is_refused(self):
        self.party.active = False
        self.party.save(update_fields=["active"])
        self.assertEqual(self._basic("studio", self.secret).status_code, 401)

    def test_a_public_client_is_refused_even_though_it_needs_no_secret(self):
        """check_client_secret returns 'current' for a public client (PKCE-only OIDC),
        so the patron gate must reject it explicitly."""
        pub = SSORelyingParty.objects.create(
            name="SPA", client_id="spa", client_type=SSORelyingParty.PUBLIC, active=True,
        )
        raw = base64.b64encode(b"spa:").decode()
        res = self.client.get(self.url, HTTP_AUTHORIZATION=f"Basic {raw}")
        self.assertEqual(res.status_code, 401)

    def test_missing_credentials_are_refused(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_no_active_platform_is_404_not_500(self):
        self.platform.active = False
        self.platform.save(update_fields=["active"])
        self.assertEqual(self._basic("studio", self.secret).status_code, 404)


class FederationConsoleTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Zenobia", author="Us", publication_year=2026, active=True)
        self.staff = User.objects.create_user("boss", "boss@x.test", "pw", is_staff=True)
        self.plain = User.objects.create_user("joe", "joe@x.test", "pw")
        self.party = SSORelyingParty.objects.create(
            name="Studio", client_id="studio", redirect_uris="https://studio.test/sso/callback/",
            allowed_scopes="openid email profile", active=True, pairing_managed=True,
        )
        self.url = reverse("sso:federation_console")

    def test_staff_sees_the_federated_platform_listed(self):
        self.client.force_login(self.staff)
        body = self.client.get(self.url).content.decode()
        self.assertIn("Studio", body)
        self.assertIn("studio.test", body)

    def test_re_pairing_renders_a_qr(self):
        self.client.force_login(self.staff)
        res = self.client.post(self.url, {"action": "repair", "pk": str(self.party.pk)})
        self.assertEqual(res.status_code, 200)
        self.assertIn("data:image/png;base64,", res.content.decode())

    def test_inviting_a_new_platform_renders_a_qr(self):
        self.client.force_login(self.staff)
        res = self.client.post(self.url, {"action": "invite", "expected_host": "delta.test",
                                          "ttl_minutes": "5", "trusted": "on"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("data:image/png;base64,", res.content.decode())
        # A dormant, inactive relying party now exists for delta.test.
        self.assertTrue(SSORelyingParty.objects.filter(name="delta.test", active=False).exists())

    def test_a_non_staff_user_is_refused(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_anonymous_is_sent_to_login(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 302)
        self.assertIn("login", res["Location"])

    def test_an_invited_platform_still_shows_the_qr_button(self):
        # The setUp party has no paired_at -> status "invited": QR is the point.
        self.client.force_login(self.staff)
        self.assertIn("Generate QR", self.client.get(self.url).content.decode())

    def test_a_paired_platform_hides_the_qr_and_offers_re_pair(self):
        from django.utils import timezone

        self.party.paired_at = timezone.now()
        self.party.save(update_fields=["paired_at"])
        self.client.force_login(self.staff)
        body = self.client.get(self.url).content.decode()
        self.assertIn("Paired", body)
        self.assertNotIn("Generate QR", body)   # the loud button is gone once paired
        self.assertIn("Re-pair", body)          # replaced by a discreet control

    def test_re_pairing_a_paired_platform_still_works(self):
        from django.utils import timezone

        self.party.paired_at = timezone.now()
        self.party.save(update_fields=["paired_at"])
        self.client.force_login(self.staff)
        res = self.client.post(self.url, {"action": "repair", "pk": str(self.party.pk)})
        self.assertEqual(res.status_code, 200)
        self.assertIn("data:image/png;base64,", res.content.decode())
