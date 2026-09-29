"""What a plan buys, what it costs, and what happens when it cannot be paid.

The tests that matter most here are the ones about NOT losing things: a lapsed
subscriber keeps every file and every row, and a failed month is written off
rather than owed. Those are promises the interface makes in words, and words are
not enforcement.
"""

from datetime import date, timedelta
from unittest import skipIf
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community

from django.apps import apps

from . import catalogue, plans, services
from .catalogue import Entitlement, registry
from .gate import ALWAYS_FREE, SubscriptionGateMiddleware, is_entitled
from .models import (
    ChargeStatus,
    CommunityDiscount,
    CommunityPlanOffer,
    Subscription,
    SubscriptionCharge,
    SubscriptionState,
    add_months,
    period_label,
    plan_for,
)

User = get_user_model()


def _host_gates_anonymous_access() -> bool:
    """True on a host that requires a login for every page.

    Zenobia does: it is one company's private system, so its ledger, staff
    directory and price list are not public documents. Other hosts have no such
    middleware and these pages stay open, which is why this is a host question
    rather than a library decision — the tests below assert the library's
    behaviour and are skipped only where the host has deliberately overridden it.
    """
    from django.conf import settings

    return any("LoginRequired" in m for m in settings.MIDDLEWARE)


#: What a platform charges is not a secret — unless the whole platform is.
needs_public_pages = skipIf(
    _host_gates_anonymous_access(),
    "this host requires a login for every page (see its middleware), so it has "
    "no public plans page to test")


#: The fixture ladder, as a FILE — because that is what a ladder is now.
#: Same three keys the shipped one has and the same tiering `tests_enforcement`
#: reads, so its truth table survives the move from rows to a registry.
FIXTURE_YAML = """
version: 1
plans:
  - key: free
    name: Free
    default: true
    units: 0
    order: 10
  - key: standard
    name: Standard
    units: 200
    order: 20
    features: [cyprian, primula]
  - key: professional
    name: Professional
    units: 600
    order: 30
    features: [cyprian, primula, aralia, mandragora]
  - key: stipend
    name: Stipend
    units: -500
    order: 44
    features: [cyprian]
"""

_FIXTURE_DIR = None
_REAL_PLANS_FILE = None


_GATE_SETTINGS = None


def setUpModule():
    """Point the registry at the fixture ladder for this whole module.

    A module-level swap rather than a decorator on seventeen classes, and a
    real FILE rather than a patched dict: the thing under test is that a file
    becomes plans, so a fixture that skipped the file would test past the
    interesting part.
    """
    global _FIXTURE_DIR, _REAL_PLANS_FILE
    import tempfile
    from pathlib import Path

    from django.conf import settings

    _FIXTURE_DIR = tempfile.mkdtemp(prefix="toto-plans-")
    path = Path(_FIXTURE_DIR) / "plans.yaml"
    path.write_text(FIXTURE_YAML)
    _REAL_PLANS_FILE = getattr(settings, "SUBSCRIPTION_PLANS_FILE", "")
    settings.SUBSCRIPTION_PLANS_FILE = str(path)
    plans.reload()
    # The gate these modules test is the library's default gate: enforcing,
    # read-only for reads. A host may run its test suite with enforcement off
    # or with reads refused (SUBSCRIPTION_ENFORCEMENT / _GATE_READS, 1.51);
    # that is its policy, not the behaviour under test here.
    global _GATE_SETTINGS
    from django.test import override_settings

    _GATE_SETTINGS = override_settings(SUBSCRIPTION_ENFORCEMENT=True,
                                       SUBSCRIPTION_GATE_READS=False)
    _GATE_SETTINGS.enable()


def tearDownModule():
    import shutil

    from django.conf import settings

    if _GATE_SETTINGS is not None:
        _GATE_SETTINGS.disable()

    settings.SUBSCRIPTION_PLANS_FILE = _REAL_PLANS_FILE
    plans.reload()
    if _FIXTURE_DIR:
        shutil.rmtree(_FIXTURE_DIR, ignore_errors=True)


def make_plans():
    """The three fixture plans, from the registry. No rows are created."""
    return (plans.plan("free"), plans.plan("standard"),
            plans.plan("professional"))


def stipend_plan():
    """The negative tier. The platform pays its holder — one mechanism, read
    in the other direction, and the only thing that puts value back into a
    hard-capped supply."""
    return plans.plan("stipend")


def _posted(data):
    """A QueryDict-ish stand-in: set_offers calls .getlist() and iterates keys."""
    from django.http import QueryDict

    query = QueryDict(mutable=True)
    for key, value in data.items():
        if isinstance(value, list):
            query.setlist(key, value)
        else:
            query[key] = value
    return query


def offer(plan, *communities):
    """Make `plan` buyable by members of `communities`. Eligibility is CLOSED
    by default, so a test that wants somebody eligible must say so."""
    for community in communities:
        CommunityPlanOffer.objects.get_or_create(
            community=community, plan_key=plan.key)


def member(name, *communities):
    user = User.objects.create_user(name, f"{name}@example.com", "pw")
    person = Person.objects.create(user=user, display_name=name)
    for community in communities:
        person.communities.add(community)
    return user


class CatalogueTests(TestCase):
    def test_the_free_codes_are_the_ones_marked_free(self):
        free = registry.free_codes()
        self.assertIn("vault", free)
        self.assertIn("assets", free)
        # Machinery is free on every plan (8/2026): plans differ by
        # functionality, not internals — everyone gets celery and workers.
        self.assertIn("workflows", free)
        self.assertIn("jess", free)
        self.assertNotIn("aralia", free)

    def test_the_economy_is_free_so_a_wallet_can_always_be_funded(self):
        """The rule that makes a paywall escapable.

        If the wallet or the plans page were sold, somebody who lapsed could
        never buy their way back in.
        """
        for code in ("assets", "quota", "subscriptions"):
            with self.subTest(code=code):
                self.assertTrue(registry.get(code).free)

    def test_a_duplicate_declaration_is_refused(self):
        from .catalogue import DuplicateEntitlement

        with self.assertRaises(DuplicateEntitlement):
            registry.register(Entitlement("vault", "Something else"))

    def test_re_registering_the_identical_row_is_fine(self):
        """Autodiscovery may import a module twice; that must not explode."""
        registry.register(registry.get("vault"))


class WhatThisHostSellsTests(TestCase):
    """`installed()` — the display filter, and the two things it asks.

    The plans page must never offer a feature this build does not serve.
    Getting that wrong is not cosmetic: somebody pays for a tile that is not
    there, and the first they hear of it is after the money moves.
    """

    def test_an_absent_app_is_not_offered(self):
        codes = {e.code for e in registry.installed()}
        for entitlement in registry.all():
            if not apps.is_installed(f"toto.{entitlement.code}"):
                self.assertNotIn(entitlement.code, codes)

    def test_an_installed_but_unmounted_app_is_not_offered(self):
        """THE case this exists for.

        zenobia keeps `toto.mandragora` in INSTALLED_APPS purely because
        `workflows.LambdaFunction` holds a live foreign key into its
        ComputeKernel, and mounts it at no URL. `apps.is_installed` says yes;
        there is no notebook editor on that host. Same for `toto.workflows`,
        deliberately unmounted there because a company-management product does
        not hand its users a DAG builder.
        """
        mounted = catalogue.mounted_app_names()
        for code in ("mandragora", "workflows"):
            entitlement = registry.get(code)
            if entitlement is None or not apps.is_installed(f"toto.{code}"):
                continue
            if code in mounted:
                continue
            self.assertNotIn(code, {e.code for e in registry.installed()})

    def test_everything_offered_is_reachable(self):
        """The claim in one line: nothing on the plans page is unreachable."""
        mounted = catalogue.mounted_app_names()
        for entitlement in registry.installed():
            with self.subTest(code=entitlement.code):
                self.assertTrue(apps.is_installed(f"toto.{entitlement.code}"))
                self.assertIn(entitlement.code, mounted)

    def test_the_mounted_names_are_what_the_gate_reads(self):
        """`mounted_app_names` collects `app_name`, which is exactly what
        `SubscriptionGateMiddleware` reads off `resolver_match` — so what is
        sold and what is enforced come from one fact rather than two."""
        from django.urls import resolve, reverse

        match = resolve(reverse("subscriptions:plans"))
        self.assertIn(match.app_name, catalogue.mounted_app_names())

    def test_it_walks_nested_includes_not_just_the_top_level(self):
        """`get_resolver().app_dict` holds only the root's direct children, so
        an app included inside a group would look unsold while being perfectly
        reachable. Under-reporting removes a feature somebody paid for."""
        from django.urls import get_resolver

        self.assertTrue(
            set(get_resolver().app_dict) <= catalogue.mounted_app_names())

    def test_a_broken_url_conf_does_not_take_the_plans_page_down(self):
        from unittest import mock

        with mock.patch("django.urls.get_resolver", side_effect=RuntimeError):
            self.assertEqual(catalogue.mounted_app_names(), set())

    def test_a_plan_card_lists_only_what_this_host_serves(self):
        """The end of the chain: a plan may name a feature this build does not
        carry — one ladder is written for every build — and the card must
        simply not show it. Unknown keys cannot reach a real plan (the
        validator refuses them), so this asks the Plan object directly."""
        from .plans import Plan

        plan = Plan(key="probe", name="Probe",
                    features=("cyprian", "mandragora", "not-a-real-app"))
        shown = {e.feature_key for e in plan.feature_rows()}
        self.assertNotIn("not-a-real-app", shown)
        if "mandragora" not in catalogue.mounted_app_names():
            self.assertNotIn("mandragora", shown)


class EligibilityTests(TestCase):
    """Who may buy what. CLOSED BY DEFAULT, which inverts the old rule.

    This class replaced PlanAudienceTests on 2026-09-02 and every assertion in
    it flipped. The table it tested said "a plan with no audience rows is
    public"; CommunityPlanOffer says "a plan nobody is offered reaches
    nobody". The tests are rewritten rather than adjusted because the sentence
    they were written to protect is the one that changed.
    """

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="T", author="t",
                                publication_year=2026, active=True)
        cls.free, cls.standard, cls.professional = make_plans()
        cls.guild = Community.objects.create(name="Guild", slug="guild")
        cls.club = Community.objects.create(name="Club", slug="club")
        cls.insider = member("elig_insider", cls.guild)
        cls.outsider = member("elig_outsider", cls.club)
        cls.loner = User.objects.create_user("elig_loner", "l@example.com", "pw")
        cls.staff = User.objects.create_user("elig_staff", "s@example.com", "pw",
                                             is_staff=True)

    def _keys(self, user):
        return {plan.key for plan in services.eligible_plans(user)}

    def test_with_no_offers_at_all_nobody_gets_a_paid_plan(self):
        """The inversion, stated once. An empty table used to mean everything
        was public; it now means nothing is for sale."""
        self.assertEqual(self._keys(self.insider), {"free"})
        self.assertEqual(self._keys(self.outsider), {"free"})
        self.assertFalse(services.is_eligible(self.insider, "professional"))

    def test_a_member_of_an_offering_community_is_eligible(self):
        offer(self.professional, self.guild)
        self.assertIn("professional", self._keys(self.insider))
        self.assertTrue(services.is_eligible(self.insider, "professional"))

    def test_a_member_of_another_community_is_not(self):
        offer(self.professional, self.guild)
        self.assertNotIn("professional", self._keys(self.outsider))
        self.assertFalse(services.is_eligible(self.outsider, "professional"))

    def test_the_default_plan_is_always_eligible_for_a_signed_in_person(self):
        """Otherwise the page denies the plan `plan_for` puts them on."""
        self.assertTrue(services.is_eligible(self.loner, "free"))
        self.assertIn("free", self._keys(self.loner))

    def test_two_offering_communities_show_the_plan_once(self):
        offer(self.professional, self.guild, self.club)
        both = member("elig_both", self.guild, self.club)
        keys = [plan.key for plan in services.eligible_plans(both)]
        self.assertEqual(keys.count("professional"), 1)

    def test_staff_see_every_plan(self):
        self.assertEqual(self._keys(self.staff),
                         {"free", "standard", "professional", "stipend"})

    def test_anonymous_is_eligible_for_nothing(self):
        self.assertEqual(services.eligible_plans(AnonymousUser()), ())
        self.assertFalse(services.is_eligible(AnonymousUser(), "free"))

    def test_an_unknown_key_is_never_eligible(self):
        self.assertFalse(services.is_eligible(self.staff, "no-such-plan"))

    def test_an_offer_naming_a_departed_plan_grants_nothing(self):
        """A key can outlive its plan — the table has no foreign key to stop
        it — and an offer of nothing must stay an offer of nothing."""
        CommunityPlanOffer.objects.create(community=self.guild,
                                          plan_key="ghost")
        self.assertFalse(services.is_eligible(self.insider, "ghost"))
        self.assertNotIn("ghost", self._keys(self.insider))

    def test_leaving_the_community_takes_the_plan_away_but_keeps_the_row(self):
        """Since 2026-09-26 the offer gates the PLAN IN FORCE, not only the
        purchase: a Community's plans are its members', so leaving it (or an
        offer withdrawn) drops the person to the default plan on the next
        request. The row is not cancelled — coming back restores it — and the
        billing sweep writes the reason down (`withdrawn`)."""
        offer(self.professional, self.guild)
        services.subscribe(self.insider, self.professional)
        self.insider.community_profile.communities.remove(self.guild)
        self.assertFalse(services.is_eligible(self.insider, "professional"))
        self.assertEqual(plan_for(self.insider).key, "free")
        self.assertEqual(Subscription.objects.get(user=self.insider).state, SubscriptionState.ACTIVE)
        self.insider.community_profile.communities.add(self.guild)
        self.assertEqual(plan_for(self.insider).key, "professional")

    def test_the_service_refuses_an_ineligible_plan_too(self):
        """The view 404s first; this is the belt to that braces, so an API or
        a management command cannot route around the rule."""
        with self.assertRaises(services.IneligiblePlan):
            services.subscribe(self.outsider, self.professional)

    def test_an_operator_can_force_past_the_check(self):
        services.subscribe(self.outsider, self.professional, force=True)
        self.assertEqual(plan_for(self.outsider).key, "professional")

    def test_an_approver_is_not_a_second_way_past_the_community_rule(self):
        """`approved_by` says who is accountable for money, not who may buy.

        The guard read `not force and approved_by is None and not
        is_eligible(...)` for a day, which made naming an approver an
        undocumented bypass — and the docstring directly above it promised
        that calling the service directly could not route around the rule.
        One bypass, and it is `force`.
        """
        approver = Person.objects.create(display_name="Eligibility approver")

        with self.assertRaises(services.IneligiblePlan):
            services.subscribe(self.insider, self.professional,
                               approved_by=approver)

    def test_force_is_the_one_door_and_it_still_opens(self):
        """The operator path an offer-less subscription needs."""
        subscription = services.subscribe(self.insider, self.professional,
                                          force=True)

        self.assertEqual(subscription.plan_key, "professional")

class OfferTabTests(TestCase):
    """The Communities tab: the grid an operator actually turns."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="T", author="t",
                                publication_year=2026, active=True)
        cls.free, cls.standard, cls.professional = make_plans()
        cls.guild = Community.objects.create(name="Guild", slug="guild")
        # The tabs are superusers' since 2026-09-26 (plans are the admin's).
        cls.staff = User.objects.create_user("tab_staff", "ts@example.com", "pw",
                                             is_staff=True, is_superuser=True)
        cls.plain = User.objects.create_user("tab_plain", "tp@example.com", "pw")

    def setUp(self):
        self.client.force_login(self.staff)

    def test_a_non_operator_is_refused(self):
        self.client.force_login(self.plain)
        self.assertEqual(
            self.client.get(reverse("subscriptions:audience")).status_code, 403)

    def test_the_columns_come_from_the_registry(self):
        response = self.client.get(reverse("subscriptions:audience"))
        self.assertEqual(response.status_code, 200)
        keys = [column["plan"].key for column in response.context["plans"]]
        self.assertEqual(keys, ["free", "standard", "professional", "stipend"])

    def test_a_plan_nobody_is_offered_is_marked_so(self):
        response = self.client.get(reverse("subscriptions:audience"))
        columns = {c["plan"].key: c for c in response.context["plans"]}
        self.assertFalse(columns["professional"]["offered"])
        self.assertContains(response, "offered to nobody")

    def test_ticking_a_box_creates_the_offer(self):
        self.client.post(reverse("subscriptions:audience"), {
            "aud-seen": [f"{self.guild.pk}-professional"],
            f"aud-{self.guild.pk}-professional": "on",
        })
        self.assertTrue(CommunityPlanOffer.objects.filter(
            community=self.guild, plan_key="professional").exists())

    def test_unticking_removes_it(self):
        offer(self.professional, self.guild)
        self.client.post(reverse("subscriptions:audience"), {
            "aud-seen": [f"{self.guild.pk}-professional"],
        })
        self.assertFalse(CommunityPlanOffer.objects.filter(
            community=self.guild, plan_key="professional").exists())

    def test_a_pair_the_form_never_rendered_is_left_alone(self):
        """Stale-page safety: diffing against what was RENDERED, not against
        the table, is what stops a plan added since being silently cleared."""
        offer(self.standard, self.guild)
        self.client.post(reverse("subscriptions:audience"), {
            "aud-seen": [f"{self.guild.pk}-professional"],
        })
        self.assertTrue(CommunityPlanOffer.objects.filter(
            community=self.guild, plan_key="standard").exists())

    def test_a_plan_key_containing_a_hyphen_survives_the_form(self):
        """`split("-")` would have dropped it from both sets, which reads as
        "the operator unticked it". The key is split on the FIRST hyphen."""
        added, _removed = services.set_offers(_posted({
            "aud-seen": [f"{self.guild.pk}-free"],
            f"aud-{self.guild.pk}-free": "on",
        }))
        self.assertEqual(added, 1)

    def test_an_unknown_plan_key_is_ignored(self):
        added, removed = services.set_offers(_posted({
            "aud-seen": [f"{self.guild.pk}-nonesuch"],
            f"aud-{self.guild.pk}-nonesuch": "on",
        }))
        self.assertEqual((added, removed), (0, 0))


class OfferAdminPageTests(TestCase):
    """The offer admin's plan field is a choice from the ladder. It was made
    by handing `choices` to a SlugField's form field, which takes none, so
    every add and change page raised TypeError (found 2026-09-28)."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="T", author="t",
                                publication_year=2026, active=True)
        cls.free, cls.standard, cls.professional = make_plans()
        cls.guild = Community.objects.create(name="Guild", slug="guild")
        cls.root = User.objects.create_superuser("offer_root", "or@example.com", "pw")

    def test_the_add_and_change_pages_render(self):
        self.client.force_login(self.root)
        offer = CommunityPlanOffer.objects.create(community=self.guild, plan_key=self.free.key)
        add = self.client.get(reverse("admin:subscriptions_communityplanoffer_add"))
        change = self.client.get(reverse("admin:subscriptions_communityplanoffer_change",
                                         args=[offer.pk]))
        self.assertEqual((add.status_code, change.status_code), (200, 200))
        self.assertContains(add, f'value="{self.standard.key}"')


class DiscountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.professional = make_plans()
        cls.students = Community.objects.create(name="Students")
        cls.founders = Community.objects.create(name="Founders")

    def test_no_community_pays_full(self):
        user = member("solo")
        self.assertEqual(services.best_discount(user, self.standard), (0, ""))
        self.assertEqual(services.billed_units(self.standard, 0), Decimal("200"))

    def test_the_best_of_several_communities_wins(self):
        CommunityDiscount.objects.create(
            community=self.students, percent=40)
        CommunityDiscount.objects.create(
            community=self.founders, percent=10)
        user = member("both", self.students, self.founders)

        percent, source = services.best_discount(user, self.standard)

        self.assertEqual(percent, 40)
        self.assertEqual(source, "Students")
        self.assertEqual(services.billed_units(self.standard, percent),
                         Decimal("120"))

    def test_one_number_applies_to_every_plan(self):
        # The rework's whole point: a community's discount is plan-agnostic,
        # so a new plan can never quietly arrive at full price for members.
        CommunityDiscount.objects.create(
            community=self.students, percent=50)
        user = member("student", self.students)

        for plan in (self.free, self.standard, self.professional):
            self.assertEqual(services.best_discount(user, plan),
                             (50, "Students"))

    def test_a_user_with_no_person_row_is_not_an_error(self):
        user = User.objects.create_user("profileless", password="pw")
        self.assertEqual(services.best_discount(user, self.standard), (0, ""))

    def test_anonymous_is_not_an_error(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(services.best_discount(AnonymousUser(), self.standard),
                         (0, ""))


class PlanResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.professional = make_plans()

    def test_no_subscription_resolves_to_the_default_plan(self):
        self.assertEqual(plan_for(member("nobody")), self.free)

    def test_a_lapsed_subscription_resolves_to_the_default_not_its_own(self):
        """The row records what they chose; this answers what they get."""
        user = member("lapsed")
        subscription = services.subscribe(user, self.professional, force=True)
        subscription.state = SubscriptionState.LAPSED
        subscription.save(update_fields=["state"])

        self.assertEqual(plan_for(user), self.free)

    def test_arrears_still_grants_because_that_is_what_grace_means(self):
        user = member("behind")
        subscription = services.subscribe(user, self.professional, force=True)
        subscription.state = SubscriptionState.ARREARS
        subscription.save(update_fields=["state"])

        self.assertEqual(plan_for(user), self.professional)


class GateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.professional = make_plans()

    def test_a_free_user_is_refused_a_paid_app(self):
        self.assertFalse(is_entitled(member("plain"), "aralia"))

    def test_a_free_user_keeps_every_free_app(self):
        user = member("plain")
        for code in ("vault", "socialhub", "assets", "quota", "subscriptions"):
            with self.subTest(code=code):
                self.assertTrue(is_entitled(user, code))

    def test_a_plan_grants_exactly_what_it_lists(self):
        user = member("buyer")
        services.subscribe(user, self.standard, force=True)

        self.assertTrue(is_entitled(user, "cyprian"))
        self.assertFalse(is_entitled(user, "aralia"))

    def test_an_app_nobody_declared_is_not_gated(self):
        """Installing a new app must not be a silent outage."""
        self.assertTrue(is_entitled(member("plain"), "some-new-app"))

    def test_a_fresh_host_is_gated_by_the_registry(self):
        """The inversion of `test_nothing_is_gated_before_any_plan_is_seeded`.

        That test asserted a real property of the old design: plans were rows,
        an unseeded host had none, `plan_for` answered None and the gate read
        that as "sells nothing, gates nothing" — so SEEDING was what turned
        gating on. A validated file is never empty, so the fully-open state is
        unreachable and enforcement is BUILD_SUBSCRIPTIONS_ENFORCE's decision
        alone, which is what that flag always claimed to be.
        """
        user = member("fresh")
        self.assertEqual(plan_for(user).key, "free")
        self.assertFalse(is_entitled(user, "aralia"))

    def test_the_always_free_list_covers_the_way_back_in(self):
        for code in ("core", "sso", "subscriptions", "assets", "quota"):
            with self.subTest(code=code):
                self.assertIn(code, ALWAYS_FREE)


class MiddlewareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.professional = make_plans()
        cls.user = member("reader")

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = SubscriptionGateMiddleware(lambda request: None)

    def _run(self, method, path="/aralia/", app_name="aralia", **extra):
        request = getattr(self.factory, method.lower())(path, **extra)
        request.user = self.user
        request.resolver_match = type("M", (), {"app_name": app_name})()
        return self.middleware.process_view(request, None, (), {}), request

    def test_a_read_is_allowed_and_marked(self):
        response, request = self._run("get")

        self.assertIsNone(response)
        self.assertTrue(request.plan_locked)

    def test_a_write_is_refused_with_402(self):
        response, _request = self._run("post")

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 402)

    def test_an_api_write_gets_json_not_a_page(self):
        import json

        response, _request = self._run("post", HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 402)
        payload = json.loads(response.content)
        self.assertEqual(payload["reason"], "subscription-required")
        self.assertEqual(payload["entitlement"], "aralia")

    def test_a_subscriber_writes_freely(self):
        services.subscribe(self.user, self.professional, force=True)

        response, request = self._run("post")

        self.assertIsNone(response)
        self.assertFalse(request.plan_locked)

    def test_an_anonymous_request_is_never_gated(self):
        from django.contrib.auth.models import AnonymousUser

        request = self.factory.post("/aralia/")
        request.user = AnonymousUser()
        request.resolver_match = type("M", (), {"app_name": "aralia"})()

        self.assertIsNone(self.middleware.process_view(request, None, (), {}))

    def test_a_free_app_is_never_gated_even_for_a_write(self):
        response, request = self._run("post", path="/vault/", app_name="vault")

        self.assertIsNone(response)
        self.assertFalse(request.plan_locked)

    def test_a_broken_lookup_opens_reads_and_closes_writes(self):
        """The asymmetry the module docstring promises.

        Delta's helper returns False on any exception, which GRANTS access on a
        database fault. That is the wrong way round for something deciding what
        somebody paid for.
        """
        from unittest import mock

        with mock.patch("toto.subscriptions.gate.is_entitled",
                        side_effect=RuntimeError("db on fire")):
            read, _ = self._run("get")
            write, _ = self._run("post")

        self.assertIsNone(read)
        self.assertEqual(write.status_code, 402)


class PeriodTests(TestCase):
    def test_month_arithmetic_clamps_to_a_short_month(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2024, 1, 31), 1), date(2024, 2, 29))

    def test_a_year_of_months_lands_on_the_same_day(self):
        self.assertEqual(add_months(date(2026, 3, 15), 12), date(2027, 3, 15))

    def test_the_label_is_the_month(self):
        self.assertEqual(period_label(date(2026, 8, 3)), "2026-08")


class MaterializeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.professional = make_plans()

    def test_one_row_per_elapsed_month(self):
        user = member("old")
        subscription = services.subscribe(user, self.standard, force=True)
        subscription.anchor_date = date.today().replace(day=1) - timedelta(days=70)
        subscription.anchor_date = subscription.anchor_date.replace(day=1)
        subscription.save(update_fields=["anchor_date"])

        services.materialize(subscription)

        self.assertGreaterEqual(subscription.charges.count(), 3)

    def test_running_it_twice_creates_nothing_new(self):
        user = member("twice")
        subscription = services.subscribe(user, self.standard, force=True)

        services.materialize(subscription)
        first = subscription.charges.count()
        services.materialize(subscription)

        self.assertEqual(subscription.charges.count(), first)

    def test_it_never_bills_the_future(self):
        user = member("future")
        subscription = services.subscribe(user, self.standard, force=True)

        services.materialize(subscription)

        labels = list(subscription.charges.values_list("period_label", flat=True))
        self.assertEqual(labels, [period_label()])


class SettleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.professional = make_plans()
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})

    def test_an_unpriced_host_settles_every_month_as_paid(self):
        """Zenobia today. A DUE row nobody can price would pile up forever."""
        user = member("unbilled")
        subscription = services.subscribe(user, self.standard, force=True)
        services.materialize(subscription)
        charge = subscription.charges.get()

        services.settle(charge)

        charge.refresh_from_db()
        self.assertEqual(charge.status, ChargeStatus.PAID)

    def test_a_negative_plan_refuses_to_be_assigned_without_an_approver(self):
        """The hole `1034f557` opened. A negative plan pays its holder every
        period out of the treasury, forever, and nothing else on the platform
        creates a recurring outbound payment — so this is the one place somebody
        could arrange to be paid, including for themselves."""
        stipend = stipend_plan()
        user = member("opportunist")

        with self.assertRaises(services.UnapprovedStipend):
            services.subscribe(user, stipend, force=True)

        self.assertFalse(Subscription.objects.filter(user=user).exists())

    def test_an_approved_negative_plan_is_allowed(self):
        stipend = stipend_plan()
        user = member("hired")
        approver = Person.objects.create(display_name="Chair")

        subscription = services.subscribe(user, stipend, approved_by=approver, force=True)

        self.assertEqual(subscription.plan, stipend)

    def test_a_positive_plan_needs_no_approver(self):
        """Paying to be here is nobody's decision but the payer's."""
        user = member("customer")
        self.assertIsNotNone(services.subscribe(user, self.standard, force=True))

    def test_a_negative_plan_pays_the_subscriber(self):
        """A stipend. Same plan, same period, same ledger — other direction.

        This is what replaced socialhub.Station (removed 8/2026): an office that fused a charter,
        four rights and a payslip into one row, of which only the payslip was
        load-bearing.
        """
        from unittest import mock

        stipend = stipend_plan()
        user = member("engineer-1")
        subscription = services.subscribe(
            user, stipend,
            approved_by=Person.objects.create(display_name="Approver engineer-1"),
            force=True)
        services.materialize(subscription)
        charge = subscription.charges.get()

        with mock.patch("toto.quota.charge.price_for", return_value=object()), \
             mock.patch("toto.quota.charge.credit") as paid:
            services.settle(charge)

        charge.refresh_from_db()
        self.assertEqual(charge.status, ChargeStatus.PAID)
        self.assertEqual(charge.units, Decimal("-500"))
        self.assertTrue(paid.called, "a negative plan must reach the credit door")
        # The magnitude is paid, not the sign: the caller chose the direction.
        self.assertEqual(paid.call_args.args[3], 500)

    def test_a_negative_plan_never_charges(self):
        """The failure that would matter most: paying somebody by debiting them."""
        from unittest import mock

        stipend = stipend_plan()
        user = member("tech-1")
        subscription = services.subscribe(
            user, stipend,
            approved_by=Person.objects.create(display_name="Approver tech-1"),
            force=True)
        services.materialize(subscription)
        charge = subscription.charges.get()

        with mock.patch("toto.quota.charge.price_for", return_value=object()), \
             mock.patch("toto.quota.charge.credit"), \
             mock.patch("toto.quota.charge.charge") as debited:
            services.settle(charge)

        self.assertFalse(debited.called, "a stipend must never debit the member")

    def test_a_discount_never_shrinks_a_stipend(self):
        """A community discount reduces what you OWE. It has no meaning when
        you are owed — and ROUND_DOWN would have shrunk it silently."""
        community = Community.objects.create(name="Perks", slug="perks")
        CommunityDiscount.objects.create(community=community, percent=20)
        stipend = stipend_plan()

        self.assertEqual(services.billed_units(stipend, 20), stipend.units)
        self.assertEqual(services.billed_units(stipend, 20), Decimal("-500"))

    def test_an_unpayable_stipend_stays_due_without_arrears(self):
        """An empty treasury is not a default. Arrears mean 'you owe us and
        access is at risk'; a member the platform could not pay owes nothing."""
        from unittest import mock

        stipend = stipend_plan()
        user = member("board-1")
        subscription = services.subscribe(
            user, stipend,
            approved_by=Person.objects.create(display_name="Approver board-1"),
            force=True)
        services.materialize(subscription)
        charge = subscription.charges.get()

        with mock.patch("toto.quota.charge.price_for", return_value=object()), \
             mock.patch("toto.quota.charge.credit",
                        side_effect=RuntimeError("treasury empty")):
            services.settle(charge)

        charge.refresh_from_db()
        subscription.refresh_from_db()
        self.assertNotEqual(charge.status, ChargeStatus.PAID)
        self.assertNotEqual(subscription.state, SubscriptionState.ARREARS)

    def test_a_free_plan_costs_nothing_and_settles(self):
        user = member("freeloader")
        subscription = services.subscribe(user, self.free, force=True)
        services.materialize(subscription)
        charge = subscription.charges.get()

        services.settle(charge)

        charge.refresh_from_db()
        self.assertEqual(charge.status, ChargeStatus.PAID)
        self.assertEqual(charge.units, Decimal("0"))

    def test_settling_twice_takes_no_second_bite(self):
        from unittest import mock

        user = member("double")
        subscription = services.subscribe(user, self.standard, force=True)
        services.materialize(subscription)
        charge = subscription.charges.get()
        services.settle(charge)

        with mock.patch("toto.quota.charge.charge") as spend:
            services.settle(charge)

        spend.assert_not_called()

    def test_an_empty_wallet_fails_the_month_and_enters_arrears(self):
        from unittest import mock

        from toto.quota.charge import InsufficientFunds

        user = member("broke")
        subscription = services.subscribe(user, self.standard, force=True)
        services.materialize(subscription)
        charge = subscription.charges.get()

        # price_for and charge are both imported inside settle(), so patching
        # them on toto.quota.charge is what the function actually looks up.
        with mock.patch("toto.quota.charge.price_for", return_value=object()), \
             mock.patch("toto.quota.charge.charge",
                        side_effect=InsufficientFunds("ASR", 200, 0, 9)):
            services.settle(charge)

        charge.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(charge.status, ChargeStatus.FAILED)
        self.assertEqual(subscription.state, SubscriptionState.ARREARS)
        self.assertIsNotNone(subscription.arrears_since)


class ArrearsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.professional = make_plans()

    def _in_arrears(self, days_ago):
        from django.utils import timezone

        user = member(f"late{days_ago}")
        subscription = services.subscribe(user, self.professional, force=True)
        subscription.state = SubscriptionState.ARREARS
        subscription.arrears_since = timezone.now() - timedelta(days=days_ago)
        subscription.save(update_fields=["state", "arrears_since"])
        return subscription

    def test_inside_the_grace_window_nothing_happens(self):
        subscription = self._in_arrears(1)

        self.assertFalse(services.lapse_if_overdue(subscription))
        self.assertEqual(subscription.state, SubscriptionState.ARREARS)

    def test_past_the_window_it_lapses(self):
        subscription = self._in_arrears(services.grace_days() + 1)

        self.assertTrue(services.lapse_if_overdue(subscription))
        self.assertEqual(subscription.state, SubscriptionState.LAPSED)

    def test_lapsing_deletes_nothing(self):
        """The promise the arrears banner makes, asserted rather than hoped."""
        from toto.vault.models import Bucket

        subscription = self._in_arrears(services.grace_days() + 1)
        bucket = Bucket.objects.create(
            name="Mine", slug=f"mine-{subscription.user.pk}",
            owner=subscription.user)

        services.lapse_if_overdue(subscription)

        self.assertTrue(Bucket.objects.filter(pk=bucket.pk).exists())
        self.assertTrue(SubscriptionCharge.objects.filter(
            subscription=subscription).count() >= 0)
        self.assertTrue(Subscription.objects.filter(pk=subscription.pk).exists())

    def test_subscribing_again_restores_access_immediately(self):
        subscription = self._in_arrears(services.grace_days() + 1)
        services.lapse_if_overdue(subscription)
        self.assertEqual(plan_for(subscription.user), self.free)

        services.subscribe(subscription.user, self.professional, force=True)

        self.assertEqual(plan_for(subscription.user), self.professional)

    def test_a_paid_month_clears_the_arrears_state(self):
        subscription = self._in_arrears(1)
        services.materialize(subscription)
        charge = subscription.charges.first()

        services.settle(charge)

        subscription.refresh_from_db()
        self.assertEqual(subscription.state, SubscriptionState.ACTIVE)
        self.assertIsNone(subscription.arrears_since)


class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.free, cls.standard, cls.professional = make_plans()
        cls.community = Community.objects.create(name="Shop", slug="shop")
        cls.user = member("shopper", cls.community)
        # Eligibility is closed by default, so a shopper who is offered
        # nothing sees nothing and can buy nothing. Offering the ladder to
        # their community is what makes this class about the VIEWS again.
        offer(cls.standard, cls.community)
        offer(cls.professional, cls.community)

    @needs_public_pages
    def test_the_plans_page_is_readable_without_logging_in(self):
        """What a platform charges is not a secret."""
        response = self.client.get(reverse("subscriptions:plans"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Standard")

    @needs_public_pages
    def test_a_card_lists_what_the_gate_will_actually_grant(self):
        response = self.client.get(reverse("subscriptions:plans"))

        row = next(r for r in response.context["rows"]
                   if r["plan"].key == "standard")
        codes = {e.code for e in row["entitlements"]}
        self.assertIn("cyprian", codes)
        # The commons and the machinery are on every plan, so a card no
        # longer repeats them — it lists only what the plan adds.
        self.assertNotIn("vault", codes)
        self.assertNotIn("workflows", codes)
        self.assertNotIn("aralia", codes)

    def test_the_discount_is_named_on_the_card(self):
        community = Community.objects.create(name="Students")
        CommunityDiscount.objects.create(community=community, percent=40)
        Person.objects.get(user=self.user).communities.add(community)
        self.client.force_login(self.user)

        response = self.client.get(reverse("subscriptions:plans"))

        row = next(r for r in response.context["rows"]
                   if r["plan"].key == "standard")
        self.assertEqual(row["discount_percent"], 40)
        self.assertEqual(row["discount_source"], "Students")

    def test_subscribing_puts_you_on_the_plan(self):
        self.client.force_login(self.user)

        self.client.post(reverse("subscriptions:subscribe", args=["professional"]))

        self.assertEqual(plan_for(self.user), self.professional)

    def test_my_page_materialises_but_never_settles(self):
        """A page render must not reach into somebody's wallet."""
        from unittest import mock

        self.client.force_login(self.user)
        services.subscribe(self.user, self.standard, force=True)

        with mock.patch("toto.subscriptions.services.settle") as settle:
            response = self.client.get(reverse("subscriptions:mine"))

        self.assertEqual(response.status_code, 200)
        settle.assert_not_called()
        self.assertEqual(
            SubscriptionCharge.objects.filter(subscription__user=self.user).count(), 1)

    def test_cancelling_keeps_the_free_plan(self):
        self.client.force_login(self.user)
        services.subscribe(self.user, self.professional, force=True)

        self.client.post(reverse("subscriptions:cancel"))

        self.assertEqual(plan_for(self.user), self.free)


class IngressTests(TestCase):
    """The seeder, which now seeds OFFERS rather than plans.

    Its old battery is gone with the table: three plans ensured, exactly one
    default survives a hand edit, and the studio→professional rename that
    carried subscribers across. Renaming a tier is an edit to plans.yaml now,
    the default is guaranteed by the validator rather than fixed up after the
    fact, and there are no rows to ensure. What is left to test is the thing
    the command actually does: make the ladder reachable.
    """

    def _seed(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("ingress_subscriptions", *args, stdout=out)
        return out.getvalue()

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="T", author="t",
                                publication_year=2026, active=True)
        cls.free, cls.standard, cls.professional = make_plans()

    def test_the_shipped_ladder_grants_every_paid_feature(self):
        """A feature declared paid and granted by NO plan hides its dashboard
        tile from everybody, superusers included, and 402s its writes wherever
        enforcement is on. That has happened twice — to the compute tier, and
        to ocr/fileservices — and both times it was found by audit. Asserted
        against the SHIPPED file, not the fixture.

        Only for the catalogue's OWN declarations (2026-09-28): a host app may
        declare its own entitlement (`<app>/entitlements.py`, zenobia's books
        are the first) and sell it in the host's ladder, which replaces this
        file there — `subscriptions.W001` checks that ladder. The shipped file
        cannot know a host's apps, and naming one would make it unusable on
        every host without that app (an unknown feature key is E001)."""
        from pathlib import Path

        shipped = plans.DEFAULT_PLANS_FILE
        self.assertEqual(plans.validate(Path(shipped)), [])

        import yaml

        raw = yaml.safe_load(Path(shipped).read_text())
        granted = {key for plan in raw["plans"] for key in plan.get("features", [])}
        for entitlement in catalogue._DEFAULTS:
            if entitlement.free:
                continue
            with self.subTest(feature=entitlement.feature_key):
                self.assertIn(entitlement.feature_key, granted)

    def test_it_offers_the_default_plan_to_every_community(self):
        """Closed by default means a fresh platform would otherwise be dark."""
        guild = Community.objects.create(name="Guild", slug="ingress-guild")
        club = Community.objects.create(name="Club", slug="ingress-club")
        self._seed()
        for community in (guild, club):
            with self.subTest(community=community.name):
                self.assertTrue(CommunityPlanOffer.objects.filter(
                    community=community, plan_key="free").exists())

    def test_running_it_twice_changes_nothing(self):
        Community.objects.create(name="Guild", slug="ingress-guild2")
        self._seed()
        before = CommunityPlanOffer.objects.count()
        self._seed()
        self.assertEqual(CommunityPlanOffer.objects.count(), before)

    def test_the_operators_community_is_made_before_anything_is_offered(self):
        """bootstrap_plans runs first (2026-09-26), so there is always at least
        one Community — the operators' — and "no communities" is unreachable."""
        out = self._seed()
        self.assertIn("Operators", out)
        self.assertTrue(Community.objects.filter(slug="operators").exists())

    def test_full_offers_the_paid_ladder_to_the_first_community(self):
        guild = Community.objects.create(name="Guild", slug="ingress-guild3")
        self._seed("--full")
        offered = set(CommunityPlanOffer.objects
                      .filter(community=guild).values_list("plan_key", flat=True))
        self.assertEqual(offered, {"free", "standard", "professional", "stipend"})

    def test_the_demo_discount_is_full_only(self):
        Community.objects.create(name="Guild", slug="ingress-guild4")
        self._seed()
        self.assertEqual(CommunityDiscount.objects.count(), 0)
        self._seed("--full")
        self.assertEqual(CommunityDiscount.objects.count(), 1)

    def test_a_broken_ladder_stops_the_seed(self):
        """ingress_all is the deploy-time leg of the same check `manage.py
        check` performs at build time."""
        import tempfile
        from pathlib import Path

        from django.core.management.base import CommandError
        from django.test import override_settings

        broken = Path(tempfile.mkdtemp()) / "broken.yaml"
        broken.write_text("version: 1\nplans: []\n")
        with override_settings(SUBSCRIPTION_PLANS_FILE=str(broken)):
            plans.reload()
            with self.assertRaises(CommandError):
                self._seed()
        plans.reload()


class ManualTests(TestCase):
    """The long-form explanation, gated on the app being installed."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.user = member("reader")

    def test_the_manual_describes_plans_where_the_app_exists(self):
        self.client.force_login(self.user)

        body = self.client.get(reverse("core:manual")).content.decode()

        self.assertIn("Your plan", body)
        # BOTH halves of what a community decides, because they are two
        # different rules and only one of them was ever written down here.
        # A community decides what a plan COSTS you (the discount) and, since
        # 2026-09-02, WHETHER YOU MAY BUY IT AT ALL — a plan nobody offers is
        # a plan nobody sees, and a reader who cannot find the tier they
        # expected needs the manual to say where to ask.
        self.assertIn("decide which plans you can choose", body)
        self.assertIn("decide what you pay", body)

    def test_it_says_nothing_is_deleted(self):
        """The promise the code keeps, written where somebody will read it."""
        self.client.force_login(self.user)

        body = self.client.get(reverse("core:manual")).content.decode()

        self.assertIn("nothing is deleted if you stop paying", body)


class NavigationTests(TestCase):
    """Subscriptions is a place in the economy strip, not a one-way trip.

    The strip's chip pointed here and the pages carried none of it back, so the
    only way out of the plan browser was the browser's own Back button. The chip
    also used to be one of TWO: "Plans" beside a "Fees" chip whose staff half
    rendered the Metered catalogue with input boxes on — the same page under a
    different name.
    """

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.free, cls.standard, cls.professional = make_plans()
        cls.community = Community.objects.create(name="Browse", slug="browse")
        cls.user = member("browser", cls.community)
        offer(cls.standard, cls.community)
        offer(cls.professional, cls.community)

    @needs_public_pages
    def test_the_plans_page_carries_the_economy_strip(self):
        body = self.client.get(reverse("subscriptions:plans")).content.decode()

        self.assertIn(reverse("quota:my_usage"), body)

    @needs_public_pages
    def test_the_strip_marks_subscriptions_as_where_you_are(self):
        body = self.client.get(reverse("subscriptions:plans")).content.decode()

        self.assertIn("Subscriptions", body)
        self.assertIn('aria-current="page"', body)

    def test_the_apps_own_sub_nav_still_works_beside_it(self):
        """`with` scopes to the include, so the outer active_tab is untouched
        and Plans/Current plan still highlight independently."""
        self.client.force_login(self.user)

        plans = self.client.get(reverse("subscriptions:plans"))
        mine = self.client.get(reverse("subscriptions:mine"))

        self.assertEqual(plans.context["active_tab"], "plans")
        self.assertEqual(mine.context["active_tab"], "mine")

    def test_you_can_browse_and_subscribe_from_the_page_the_chip_points_at(self):
        """The whole ask: the chip leads somewhere you can actually act."""
        self.client.force_login(self.user)

        body = self.client.get(reverse("subscriptions:plans")).content.decode()

        self.assertIn(reverse("subscriptions:subscribe", args=["standard"]),
                      body)
        self.assertIn(reverse("subscriptions:subscribe", args=["professional"]), body)


class LockedPageChromeTests(TestCase):
    """The 402 page renders as a platform page.

    ``locked.html`` extends ``oya/base.html``, and the palette on this platform
    is a Platform record rather than a stylesheet — so a bare ``render`` here
    produced an unstyled refusal on the one page whose whole job is to persuade
    somebody to subscribe. The gate is middleware, so it cannot reach
    ``views._render`` without importing a module that imports it back.
    """

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.free, cls.standard, cls.professional = make_plans()
        cls.user = member("locked-out")

    def _refuse(self):
        factory = RequestFactory()
        middleware = SubscriptionGateMiddleware(lambda request: None)
        request = factory.post("/aralia/")
        request.user = self.user
        request.resolver_match = type("M", (), {"app_name": "aralia"})()
        return middleware.process_view(request, None, (), {})

    def test_the_refusal_carries_the_platform_and_theme(self):
        response = self._refuse()

        self.assertEqual(response.status_code, 402)
        body = response.content.decode()
        self.assertIn("Test", body)

    def test_it_is_still_a_402_and_still_names_the_plans_page(self):
        response = self._refuse()

        self.assertEqual(response.status_code, 402)
        self.assertIn(reverse("subscriptions:plans"),
                      response.content.decode())

    def test_a_missing_platform_costs_the_styling_and_not_the_refusal(self):
        """PageProcessor raises Http404 with no active Platform row. In
        middleware that would answer "no such page" to every gated write on a
        host that is still being set up."""
        Platform.objects.update(active=False)

        response = self._refuse()

        self.assertEqual(response.status_code, 402)


class DiscountTabTests(TestCase):
    """The Discounts tab: who may open it, and what saving does."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.free, cls.standard, cls.professional = make_plans()
        cls.students = Community.objects.create(name="Students")
        cls.founders = Community.objects.create(name="Founders")
        cls.member_user = member("plain")
        cls.staff = User.objects.create_user("staffer", password="pw")
        cls.staff.is_staff = cls.staff.is_superuser = True     # the tabs are superusers' (2026-09-26)
        cls.staff.save(update_fields=["is_staff", "is_superuser"])

    def test_members_cannot_open_it(self):
        self.client.force_login(self.member_user)
        response = self.client.get(reverse("subscriptions:discounts"))
        self.assertEqual(response.status_code, 403)

    def test_the_tab_only_renders_for_staff(self):
        url = reverse("subscriptions:discounts")
        self.client.force_login(self.member_user)
        self.assertNotIn(url, self.client.get(
            reverse("subscriptions:plans")).content.decode())
        self.client.force_login(self.staff)
        self.assertIn(url, self.client.get(
            reverse("subscriptions:plans")).content.decode())

    def test_it_lists_every_community_including_the_zeroes(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("subscriptions:discounts"))
        names = [row["community"].name for row in response.context["rows"]]
        self.assertEqual(names, ["Founders", "Students"])
        self.assertEqual([row["percent"] for row in response.context["rows"]],
                         [0, 0])

    def test_saving_sets_one_number_that_applies_to_every_plan(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("subscriptions:discounts"), {
            f"discount-{self.students.pk}": "40",
            f"discount-{self.founders.pk}": "0",
        })
        discount = CommunityDiscount.objects.get(community=self.students)
        self.assertEqual(discount.percent, 40)
        self.assertFalse(CommunityDiscount.objects.filter(
            community=self.founders).exists())
        # One number, every plan — the whole point of the rework.
        user = member("student", self.students)
        for plan in (self.standard, self.professional):
            self.assertEqual(services.best_discount(user, plan),
                             (40, "Students"))

    def test_zero_clears_an_existing_row(self):
        CommunityDiscount.objects.create(community=self.students, percent=25)
        self.client.force_login(self.staff)
        self.client.post(reverse("subscriptions:discounts"),
                         {f"discount-{self.students.pk}": "0"})
        self.assertFalse(CommunityDiscount.objects.exists())

    def test_a_typo_is_ignored_not_saved_as_zero(self):
        # A discount silently cancelled by a stray keystroke is worse than a
        # rejected edit: the operator sees the old value and tries again.
        CommunityDiscount.objects.create(community=self.students, percent=30)
        self.client.force_login(self.staff)
        for bad in ("", "abc", "-5", "150"):
            with self.subTest(bad=bad):
                self.client.post(reverse("subscriptions:discounts"),
                                 {f"discount-{self.students.pk}": bad})
                self.assertEqual(
                    CommunityDiscount.objects.get(
                        community=self.students).percent, 30)

    def test_percent_is_never_negative_at_the_database(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises((IntegrityError, ValueError)):
            with transaction.atomic():
                CommunityDiscount.objects.create(
                    community=self.students, percent=-1)

    def test_over_a_hundred_is_refused_by_the_constraint(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CommunityDiscount.objects.create(
                    community=self.students, percent=101)

    def test_one_row_per_community(self):
        from django.db import IntegrityError, transaction

        CommunityDiscount.objects.create(community=self.students, percent=10)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CommunityDiscount.objects.create(
                    community=self.students, percent=20)
