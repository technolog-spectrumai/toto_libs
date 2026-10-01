import base64
import os
import unittest
from unittest.mock import patch

from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.auth_cooldown import CAPTCHA_RETRY_COOLDOWN_SESSION_KEY
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.captcha import generate_code_captcha
from toto.socialhub.models import (
    Community,
    MembershipApplication,
    ReferenceRequest,
)

User = get_user_model()


class CodeCaptchaTests(TestCase):
    def test_returns_png_data_uri(self):
        uri = generate_code_captcha("482915")
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        raw = base64.b64decode(uri.split(",", 1)[1])
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")

    def test_spurious_letters_count_is_configurable(self):
        # Both call forms should succeed; the override just changes decoy count.
        self.assertTrue(generate_code_captcha("123456", spurious_letters=0))
        with override_settings(SOCIALHUB_CAPTCHA_SPURIOUS_LETTERS=12):
            self.assertTrue(generate_code_captcha("123456"))


class DefaultCommunityIngressTests(TestCase):
    """Non-full ingress seeds the DEFAULT_COMMUNITY (deploy YAML env) and adds
    the admin person to it; without the env var nothing is created."""

    def setUp(self):
        self.admin_user = User.objects.create_user(username="admin", password="x")
        self.admin_person = Person.objects.create(
            user=self.admin_user, display_name="Founder"
        )

    def _run_ingress(self, community=""):
        env = {"DEFAULT_COMMUNITY": community, "ADMIN_USERNAME": "admin"}
        with patch.dict(os.environ, env):
            call_command("ingress_socialhub")  # no --full

    def test_creates_community_and_adds_admin_person(self):
        self._run_ingress(community="Our-community")
        community = Community.objects.get(name="Our-community")
        self.assertIn(self.admin_person, community.members.all())

    def test_no_env_var_creates_nothing(self):
        self._run_ingress(community="")
        self.assertEqual(Community.objects.count(), 0)

    def test_idempotent_across_reboots(self):
        self._run_ingress(community="Our-community")
        self._run_ingress(community="Our-community")
        self.assertEqual(Community.objects.filter(name="Our-community").count(), 1)
        community = Community.objects.get(name="Our-community")
        self.assertEqual(community.members.count(), 1)

    def test_missing_admin_person_still_creates_community(self):
        self.admin_person.delete()
        self._run_ingress(community="Our-community")
        community = Community.objects.get(name="Our-community")
        self.assertEqual(community.members.count(), 0)
class ApplicationSuccessViewTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Toto", author="Test", publication_year=2026)
        self.community = Community.objects.create(name="Hill Collective")
        self.application = MembershipApplication.objects.create(
            email="newcomer@example.com",
            community=self.community,
            code="246810",
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )

    def test_renders_captcha_flow_copy(self):
        # The verification code is never emailed — the page always points the
        # applicant at the code-image (CAPTCHA) verification step.
        res = self.client.get(
            reverse("socialhub:application_success", args=[self.application.email])
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "code image")


class VerifyCaptchaViewTests(TestCase):
    def setUp(self):
        # PageProcessor (used by the view) requires an active Platform.
        Platform.objects.create(site_name="Toto", author="Test", publication_year=2026)
        self.community = Community.objects.create(name="Hill Collective")
        self.application = MembershipApplication.objects.create(
            email="newcomer@example.com",
            community=self.community,
            code="246810",
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )

    def _verify_url(self):
        return reverse(
            "socialhub:membership_verification", args=[self.application.email]
        )

    def test_shows_captcha(self):
        res = self.client.get(self._verify_url())
        self.assertEqual(res.status_code, 200)
        self.assertIn("captcha_image", res.context)
        self.assertTrue(res.context["captcha_image"].startswith("data:image/png;base64,"))

    def test_captcha_is_the_only_verification_path(self):
        # The code is never emailed — the CAPTCHA is always the verification path.
        res = self.client.get(self._verify_url())
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.context["captcha_image"].startswith("data:image/png;base64,"))

    def test_wrong_code_starts_cooldown_in_captcha_mode(self):
        res = self.client.post(self._verify_url(), {"code": "000000"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["error"], "Invalid code or username.")
        self.assertGreater(res.context["cooldown_remaining"], 0)
        self.assertIn(CAPTCHA_RETRY_COOLDOWN_SESSION_KEY, self.client.session)

    def test_second_attempt_blocked_during_cooldown(self):
        self.client.post(self._verify_url(), {"code": "000000"})
        # Even the *correct* code is refused while the cooldown is active.
        res = self.client.post(self._verify_url(), {"code": "246810"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("Please wait", res.context["error"])
        self.application.refresh_from_db()
        self.assertNotEqual(self.application.status, "verified")

    def test_correct_code_redirects_and_leaves_no_cooldown(self):
        res = self.client.post(self._verify_url(), {"code": "246810"})
        self.assertEqual(res.status_code, 302)
        self.assertNotIn(CAPTCHA_RETRY_COOLDOWN_SESSION_KEY, self.client.session)

    @override_settings(CAPTCHA_RETRY_COOLDOWN_SECONDS=0)
    def test_zero_setting_disables_cooldown(self):
        self.client.post(self._verify_url(), {"code": "000000"})
        # With the cooldown disabled, the correct code works on the next try.
        res = self.client.post(self._verify_url(), {"code": "246810"})
        self.assertEqual(res.status_code, 302)

    def test_cooldown_applies_in_captcha_mode(self):
        # CAPTCHA mode (and its retry cooldown) does not depend on email config at all.
        res = self.client.post(self._verify_url(), {"code": "000000"})
        self.assertEqual(res.status_code, 200)
        self.assertGreater(res.context["cooldown_remaining"], 0)
        self.assertIn(CAPTCHA_RETRY_COOLDOWN_SESSION_KEY, self.client.session)


class ReferenceRequestPasswordTests(TestCase):
    """Optional password on the endorsement (reference) request: it is stored on
    the applicant immediately, but the account is only activated once a referrer
    accepts — so they can log in directly the moment they're approved."""

    def setUp(self):
        Platform.objects.create(site_name="Toto", author="Test", publication_year=2026)
        self.community = Community.objects.create(name="Cedar Guild")
        # The applicant's user as created at apply time: inactive, no usable
        # password, and a login username distinct from their email.
        self.applicant = User.objects.create(
            username="janek", email="applicant@example.com", is_active=False
        )
        self.application = MembershipApplication.objects.create(
            email="applicant@example.com",
            community=self.community,
            code="111222",
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )
        # An existing member who can endorse.
        ref_user = User.objects.create_user(username="member", password="x")
        self.referrer = Person.objects.create(user=ref_user, display_name="Member")
        self.referrer.communities.add(self.community)
        # The applicant types the code first: the step is that browser's
        # (2026-10-01, tests_reference_session).
        self.client.post(reverse("socialhub:membership_verification",
                                 args=["applicant@example.com"]), {"code": "111222"})

    def _url(self):
        return reverse("socialhub:reference_request", args=[self.application.id])

    def test_password_set_now_but_user_activated_only_on_accept(self):
        res = self.client.post(
            self._url(),
            {"referrer": self.referrer.id, "message": "vouch", "password": "s3cret-pw"},
        )
        self.assertEqual(res.status_code, 302)

        self.applicant.refresh_from_db()
        self.assertTrue(self.applicant.check_password("s3cret-pw"))  # password stored…
        self.assertFalse(self.applicant.is_active)                   # …but not active yet
        self.assertIsNone(authenticate(username="janek", password="s3cret-pw"))

        # Accepting the reference activates the account; now they can log in with
        # their chosen username (not their email).
        ref = ReferenceRequest.objects.get(application=self.application)
        ref.status = "accepted"
        ref.save()

        self.applicant.refresh_from_db()
        self.assertTrue(self.applicant.is_active)
        self.assertIsNotNone(authenticate(username="janek", password="s3cret-pw"))

    def test_password_is_optional(self):
        res = self.client.post(
            self._url(), {"referrer": self.referrer.id, "message": "vouch"}
        )
        self.assertEqual(res.status_code, 302)
        self.assertTrue(ReferenceRequest.objects.filter(application=self.application).exists())
        self.applicant.refresh_from_db()
        self.assertFalse(self.applicant.check_password("anything"))  # no real password set


class ReferencePatronTests(TestCase):
    """Accepting a reference now writes ``Person.patron`` — the forward fix for
    the gap password recovery (sso_core.recovery) works around: the field was
    historically written by NOTHING in this pipeline, so the person who
    actually vouched lived only on the ReferenceRequest row."""

    def setUp(self):
        Platform.objects.create(site_name="Toto", author="Test", publication_year=2026)
        self.community = Community.objects.create(name="Cedar Guild")
        self.applicant = User.objects.create(
            username="mentee", email="mentee@example.com", is_active=False
        )
        self.application = MembershipApplication.objects.create(
            email="mentee@example.com",
            community=self.community,
            code="111222",
            verified_at=timezone.now(),
            status="verified",
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )
        ref_user = User.objects.create_user(username="voucher", password="x")
        self.referrer = Person.objects.create(user=ref_user, display_name="Voucher")
        self.referrer.communities.add(self.community)
        self.ref = ReferenceRequest.objects.create(
            application=self.application, referrer=self.referrer,
        )

    def _accept(self):
        self.ref.status = "accepted"
        self.ref.save()

    def test_accepting_sets_the_referrer_as_patron(self):
        self._accept()
        person = Person.objects.get(user=self.applicant)
        self.assertEqual(person.patron_id, self.referrer.pk)

    def test_an_existing_patron_is_never_overwritten(self):
        # A patron set on purpose — by an admin, or by an earlier acceptance —
        # outranks the pipeline: accepting a second reference must not
        # reassign whose mentee this person is.
        other_user = User.objects.create_user(username="elder", password="x")
        elder = Person.objects.create(user=other_user, display_name="Elder")
        person = Person.objects.create(
            user=self.applicant, display_name="Mentee", patron=elder,
        )
        self._accept()
        person.refresh_from_db()
        self.assertEqual(person.patron_id, elder.pk)


class MembershipApplicationUsernameTests(TestCase):
    """The signup form takes a login username distinct from the email; the email
    stays the key for the application/verification flow."""

    def setUp(self):
        Platform.objects.create(site_name="Toto", author="Test", publication_year=2026)
        self.community = Community.objects.create(name="Cedar Guild")
        # An application needs a notice to accept (2026-10-01).
        from toto.socialhub.models import PrivacyNotice

        PrivacyNotice.objects.create(version=1, text_pl="Informacja", text_en="Notice")

    def _url(self):
        return reverse("socialhub:membership_application")

    def test_signup_creates_user_with_chosen_username(self):
        res = self.client.post(
            self._url(),
            {"username": "janek", "email": "janek@example.com", "community": self.community.id,
             "privacy_version": 1, "privacy_accept": "on"},
        )
        self.assertEqual(res.status_code, 302)
        user = User.objects.get(username="janek")          # username is what they chose…
        self.assertEqual(user.email, "janek@example.com")  # …email is separate
        self.assertFalse(user.is_active)                   # inactive until approved
        self.assertTrue(MembershipApplication.objects.filter(email="janek@example.com").exists())

    def test_duplicate_username_is_rejected(self):
        User.objects.create_user(username="taken", password="x")
        res = self.client.post(
            self._url(),
            {"username": "taken", "email": "new@example.com", "community": self.community.id,
             "privacy_version": 1, "privacy_accept": "on"},
        )
        self.assertEqual(res.status_code, 200)  # re-renders with a form error
        self.assertFalse(MembershipApplication.objects.filter(email="new@example.com").exists())



def _host_gates_anonymous_access() -> bool:
    """True on a host that requires a login for every page.

    Zenobia does: it is one company's private system, so its staff roster and
    its list of departments are not public documents — closing them was the
    point. Other hosts keep these pages open, so the assertions below are still
    the library's real behaviour and are skipped only where a host has
    deliberately overridden it.
    """
    from django.conf import settings

    return any("LoginRequired" in m for m in settings.MIDDLEWARE)


needs_public_pages = unittest.skipIf(
    _host_gates_anonymous_access(),
    "this host requires a login for every page (see its middleware), so it has "
    "no public roster or community list to test")


@needs_public_pages
class StatuteTests(TestCase):
    """The statute: a vault PDF on the community, for every kind of community."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})

    def test_every_org_type_may_carry_one(self):
        """Not only companies — the field is the community's, whatever it is."""
        for org_type, _label in Community.ORG_TYPES:
            with self.subTest(org_type=org_type):
                community = Community.objects.create(
                    name=f"statute-{org_type}", org_type=org_type)
                self.assertIsNone(community.statute)

    def test_deleting_the_pdf_never_deletes_the_community(self):
        """SET_NULL is the point: the texlab/aralia precedent, not notarius's
        CASCADE — a vault cleanup must not erase an institution."""
        import tempfile

        from django.contrib.auth import get_user_model
        from django.core.files.base import ContentFile
        from django.test import override_settings

        from toto.vault.models import VaultFile

        owner = get_user_model().objects.create_user("keeper", password="pw")
        with override_settings(MEDIA_ROOT=tempfile.mkdtemp()):
            pdf = VaultFile(owner=owner, title="statute.pdf",
                            key="statute-pdf", file_type="pdf")
            pdf.file.save("statute.pdf", ContentFile(b"%PDF-1.4"), save=False)
            pdf.content_hash = pdf.create_hash()
            pdf.save()
            community = Community.objects.create(name="Chartered", statute=pdf)

            pdf.delete()

        community.refresh_from_db()
        self.assertIsNone(community.statute)

    def test_the_company_type_has_existed_since_0001(self):
        """A company is a community whose org_type says so — the whole premise
        of retiring the Business Center."""
        self.assertIn(Community.COMPANY,
                      {value for value, _ in Community.ORG_TYPES})


@needs_public_pages
class CommunityListFilterTests(TestCase):
    """?org_type=company is how companies are found now."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        Community.objects.create(name="The Firm",
                                 org_type=Community.COMPANY)
        Community.objects.create(name="The Choir",
                                 org_type=Community.NON_PROFIT)

    def test_the_filter_narrows_to_one_kind(self):
        response = self.client.get("/socialhub/communities/?org_type=company")

        names = {c.name for c in response.context["communities"]}
        self.assertEqual(names, {"The Firm"})

    def test_no_filter_lists_everything(self):
        response = self.client.get("/socialhub/communities/")

        names = {c.name for c in response.context["communities"]}
        self.assertEqual(names, {"The Firm", "The Choir"})

    def test_an_unknown_value_narrows_to_nothing(self):
        """A filter that quietly ignores itself is worse than an empty page."""
        response = self.client.get("/socialhub/communities/?org_type=cabal")

        self.assertEqual(len(response.context["communities"]), 0)




class ProfileAddressPrivacyTests(TestCase):
    """The profile page must not undo the People map's opt-in.

    Both of these were live: `ProfileListView` had no `LoginRequiredMixin`, so
    the whole member roster was readable without an account; and the address
    block rendered to any signed-in user, so somebody the map refused could read
    the same address one click away. An opt-in with either of these still open
    is decoration.
    """

    def setUp(self):
        Platform.objects.get_or_create(
            active=True,
            defaults={"site_name": "Test", "author": "t",
                      "publication_year": 2026})
        User = get_user_model()
        from toto.locations.models import Address

        self.address = Address.objects.create(
            street="Sekretna 1", locality_name="Town",
            latitude=52.2297, longitude=21.0122)
        self.owner_user = User.objects.create_user("owner", password="pw")
        self.owner = Person.objects.create(
            user=self.owner_user, display_name="Owner", address=self.address)
        self.nosy_user = User.objects.create_user("nosy", password="pw")
        Person.objects.create(user=self.nosy_user, display_name="Nosy")

    def _detail_as(self, user):
        if user is not None:
            self.client.force_login(user)
        return self.client.get(
            reverse("socialhub:profile_details", args=[self.owner.slug]))

    def test_the_roster_refuses_an_anonymous_visitor(self):
        response = self.client.get(reverse("socialhub:profile_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_stranger_does_not_see_an_address_that_is_not_shared(self):
        self.assertNotContains(self._detail_as(self.nosy_user), "Sekretna")

    def test_a_stranger_sees_a_shared_address(self):
        from toto.people.models import LocationSharing

        self.owner.location_sharing = LocationSharing.EXACT
        self.owner.save(update_fields=["location_sharing"])
        self.assertContains(self._detail_as(self.nosy_user), "Sekretna")

    def test_an_approximate_sharer_shows_a_locality_not_a_street(self):
        """The page must coarsen exactly as the map does, or the map's
        coarsening is pointless."""
        from toto.people.models import LocationSharing

        self.owner.location_sharing = LocationSharing.APPROXIMATE
        self.owner.save(update_fields=["location_sharing"])
        response = self._detail_as(self.nosy_user)
        self.assertNotContains(response, "Sekretna")
        self.assertContains(response, "Town")

    def test_you_always_see_your_own_address_in_full(self):
        """Sharing off must not hide your own street from you — that reads as
        lost data rather than as the setting working."""
        self.assertContains(self._detail_as(self.owner_user), "Sekretna")

    def test_switching_sharing_on_and_off(self):
        from toto.people.models import LocationSharing

        self.client.force_login(self.owner_user)
        url = reverse("socialhub:set_location_sharing")
        self.client.post(url, {"location_sharing": "exact"})
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.location_sharing, LocationSharing.EXACT)

        self.client.post(url, {"location_sharing": "off"})
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.location_sharing, LocationSharing.OFF)

    def test_a_nonsense_setting_is_refused(self):
        self.client.force_login(self.owner_user)
        self.client.post(reverse("socialhub:set_location_sharing"),
                         {"location_sharing": "everyone-forever"})
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.location_sharing, "off")

    def test_sharing_cannot_be_set_by_an_anonymous_caller(self):
        response = self.client.post(reverse("socialhub:set_location_sharing"),
                                    {"location_sharing": "exact"})
        self.assertEqual(response.status_code, 302)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.location_sharing, "off")
