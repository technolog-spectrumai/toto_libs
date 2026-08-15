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
    Station,
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


class StationIngressTests(TestCase):
    """`--full` seeds the offices, and the membership that makes them holdable.

    `Station.clean()` refuses a holder who does not belong to the community the
    office serves, so a seed that forgot to enrol them would quietly produce a
    roster of vacancies — which looks like working code and is not.
    """

    @classmethod
    def setUpTestData(cls):
        with patch.dict(os.environ, {"DEFAULT_COMMUNITY": "", "ADMIN_USERNAME": ""}):
            call_command("ingress_socialhub", full=True, verbosity=0)

    def test_the_roster_is_not_empty(self):
        self.assertGreaterEqual(Station.objects.count(), 4)

    def test_every_holder_belongs_to_the_community_it_serves(self):
        """The requirement the model enforces — asserted on the seed itself."""
        held = Station.objects.filter(holder__isnull=False)
        self.assertTrue(held.exists())
        for station in held:
            with self.subTest(station=station.name):
                self.assertTrue(
                    station.holder.communities.filter(
                        pk=station.serves_id).exists())

    def test_a_seeded_station_would_survive_its_own_validation(self):
        for station in Station.objects.filter(holder__isnull=False):
            with self.subTest(station=station.name):
                station.full_clean(exclude=["slug"])

    def test_one_office_is_vacant_because_an_office_outlives_its_holder(self):
        self.assertTrue(
            Station.objects.filter(holder__isnull=True, active=True).exists())

    def test_an_office_carries_headroom_without_touching_what_it_pays(self):
        """A station grants LIMITS, never a discount — the whole design rests
        on a holder paying exactly what anyone else pays."""
        from toto.socialhub.privileges import limit_multiplier_for

        registrar = Station.objects.get(name="Registrar")
        self.assertGreater(registrar.limit_multiplier, 1)
        self.assertEqual(limit_multiplier_for(registrar.holder.user),
                         registrar.limit_multiplier)

    def test_an_unpaid_office_is_an_ordinary_thing_to_be(self):
        self.assertTrue(Station.objects.filter(stipend=0, holder__isnull=False).exists())

    def test_running_it_twice_does_not_duplicate_the_roster(self):
        before = Station.objects.count()
        with patch.dict(os.environ, {"DEFAULT_COMMUNITY": "", "ADMIN_USERNAME": ""}):
            call_command("ingress_socialhub", full=True, verbosity=0)
        self.assertEqual(Station.objects.count(), before)


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
            verified_at=timezone.now(),
            status="verified",
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )
        # An existing member who can endorse.
        ref_user = User.objects.create_user(username="member", password="x")
        self.referrer = Person.objects.create(user=ref_user, display_name="Member")
        self.referrer.communities.add(self.community)

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


class MembershipApplicationUsernameTests(TestCase):
    """The signup form takes a login username distinct from the email; the email
    stays the key for the application/verification flow."""

    def setUp(self):
        Platform.objects.create(site_name="Toto", author="Test", publication_year=2026)
        self.community = Community.objects.create(name="Cedar Guild")

    def _url(self):
        return reverse("socialhub:membership_application")

    def test_signup_creates_user_with_chosen_username(self):
        res = self.client.post(
            self._url(),
            {"username": "janek", "email": "janek@example.com", "community": self.community.id},
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
            {"username": "taken", "email": "new@example.com", "community": self.community.id},
        )
        self.assertEqual(res.status_code, 200)  # re-renders with a form error
        self.assertFalse(MembershipApplication.objects.filter(email="new@example.com").exists())


class StationListChromeTests(TestCase):
    """The offices roster renders as a platform page, not a bare template.

    ``PageProcessor.decorate`` is what supplies ``platform``, ``theme``,
    ``font``, ``logo`` and the header navigation. The theme on this platform is
    a database record rather than a stylesheet, so a template extending
    ``oya/base.html`` rendered through a bare ``render()`` comes out with no
    palette at all — which is exactly how this page looked. Every other view
    module in socialhub already went through the decorator; this one did not.
    """

    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test Platform",
            defaults={"author": "t", "publication_year": 2026, "active": True})

    def test_the_page_carries_the_platform_context(self):
        response = self.client.get(reverse("socialhub:station_list"))

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["platform"])
        self.assertEqual(response.context["platform"]["site_name"],
                         "Test Platform")

    def test_the_theme_reaches_the_template(self):
        """The palette is the thing that was missing."""
        response = self.client.get(reverse("socialhub:station_list"))

        self.assertIn("theme", response.context)
        self.assertIn("font", response.context)

    def test_it_is_still_public(self):
        """An institution nobody can see is not an institution."""
        self.assertEqual(
            self.client.get(reverse("socialhub:station_list")).status_code, 200)


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


class CommunityElectorateTests(TestCase):
    """The polls seam: membership is the register, one voice each."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.polls"):
            raise unittest.SkipTest("polls is not installed on this host")

    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        self.community = Community.objects.create(name="Voters")

    def test_the_registry_carries_the_community_default(self):
        import toto.socialhub.electorates as module
        from toto.polls import electorates as registry_module
        from toto.polls.models import SCOPE_COMMUNITY

        self.assertEqual(
            registry_module._SCOPE_DEFAULTS.get(SCOPE_COMMUNITY),
            module.COMMUNITY_MEMBERS)

    def test_members_vote_and_strangers_do_not(self):
        from django.contrib.auth import get_user_model

        from toto.people.models import Person
        from toto.polls.models import Kind, Question, SCOPE_COMMUNITY
        from toto.socialhub.electorates import CommunityElectorate

        User = get_user_model()
        member_user = User.objects.create_user("member", password="pw")
        member = Person.objects.create(user=member_user, display_name="Member")
        member.communities.add(self.community)
        stranger = User.objects.create_user("stranger", password="pw")

        question = Question.objects.create(
            title="Adopt the budget", question_text="Well?", kind=Kind.VOTE,
            scope_type=SCOPE_COMMUNITY, scope_id=str(self.community.pk))
        electorate = CommunityElectorate(self.community)

        self.assertTrue(electorate.standing(question, member_user).allowed)
        self.assertFalse(electorate.standing(question, stranger).allowed)
        self.assertEqual(electorate.size(question), 1)

    def test_a_vote_from_another_community_is_refused_by_the_engine(self):
        from django.contrib.auth import get_user_model

        from toto.polls.models import Kind, Question, SCOPE_COMMUNITY
        from toto.socialhub.electorates import CommunityElectorate

        other = Community.objects.create(name="Elsewhere")
        user = get_user_model().objects.create_user("anyone", password="pw")
        question = Question.objects.create(
            title="Foreign vote", question_text="Well?", kind=Kind.VOTE,
            scope_type=SCOPE_COMMUNITY, scope_id=str(other.pk))

        standing = CommunityElectorate(self.community).standing(question, user)

        self.assertFalse(standing.allowed)
        self.assertIn("another community", standing.reason)
