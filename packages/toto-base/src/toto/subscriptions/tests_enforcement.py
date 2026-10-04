"""Full enforcement: every plan × state × tier × method, and the API wire.

The gate refuses WRITES with 402 for users whose current plan does not grant
the app; reads pass and are marked. This module walks the whole truth table
so a plan or state change can never silently widen or narrow access:

  plans:  none (never subscribed) / free / standard / professional
  states: active / arrears (grace: still grants) / lapsed / cancelled
          (both resolve to the DEFAULT plan, not the chosen one)
  tiers:  free-by-catalogue apps (vault, and the machinery: workflows),
          a lower-tier app, a higher-tier app, an unknown code, and an
          ALWAYS_FREE one

The codes standing in for those last two are `cyprian` and `aralia`, and they
come from `tests.make_plans()`, which is a FIXTURE with entitlement lists of its
own. They are examples chosen to exercise the matrix, not a statement about what
the shipped ladder sells — `ingress_subscriptions.PLANS` is that, and since
8/2026 it puts cyprian on Professional with the other two rich editors.
`zenobia.tests.test_editors_are_gated` asserts the shipped tiering; this module
asserts the mechanism, and the two must not be read as one.
  methods: GET (never 402; plan_locked marks) / POST (402 iff not granted)

Run only where a gate stanza names this module (zenobia's does, with
BUILD_SUBSCRIPTIONS=1):

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.subscriptions.tests_enforcement
"""
import json

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, modify_settings

from toto.core.models import Platform

from . import services
from .gate import SubscriptionGateMiddleware, is_entitled, plan_exempt
from .models import Subscription, SubscriptionState
from .tests import make_plans
from .tests import setUpModule as _install_fixture_ladder
from .tests import tearDownModule as _restore_shipped_ladder


def setUpModule():
    """Borrow tests.py's fixture ladder.

    This module can run on its own — the gate names it separately — so it
    cannot rely on the other module having installed the file first.
    """
    _install_fixture_ladder()


def tearDownModule():
    _restore_shipped_ladder()

User = get_user_model()

GATE = "toto.subscriptions.gate.SubscriptionGateMiddleware"

#: (fixture name, chosen plan attr or None, state or None)
COMBOS = [
    ("never-subscribed", None, None),
    ("free-active", "free", SubscriptionState.ACTIVE),
    ("standard-active", "standard", SubscriptionState.ACTIVE),
    ("standard-arrears", "standard", SubscriptionState.ARREARS),
    ("standard-lapsed", "standard", SubscriptionState.LAPSED),
    ("standard-cancelled", "standard", SubscriptionState.CANCELLED),
    ("professional-active", "professional", SubscriptionState.ACTIVE),
    ("professional-arrears", "professional", SubscriptionState.ARREARS),
    ("professional-lapsed", "professional", SubscriptionState.LAPSED),
    ("professional-cancelled", "professional", SubscriptionState.CANCELLED),
]

#: code -> {combo name -> entitled?}. Against `make_plans()`, not the shipped
#: ladder: vault is catalogue-free (everyone); cyprian is on both fixture plans;
#: aralia is on the higher one only; arrears keeps granting (the grace window's
#: whole point); lapsed/cancelled fall back to the default plan, which grants
#: nothing beyond the free tier.
def expected(code, combo):
    # workflows is here on purpose: machinery is free on every plan (8/2026),
    # so it must answer True for a never-subscribed user exactly like vault.
    if code in ("vault", "workflows", "no-such-app", "assets"):
        return True
    paying = combo.endswith("-active") or combo.endswith("-arrears")
    if code == "cyprian":
        return paying and combo.startswith(("standard", "professional"))
    if code == "aralia":
        return paying and combo.startswith("professional")
    raise AssertionError(code)


class MatrixTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.free, cls.standard, cls.professional = make_plans()
        cls.users = {}
        for combo, plan_attr, state in COMBOS:
            user = User.objects.create_user(combo, password="x")
            if plan_attr is not None:
                subscription = services.subscribe(
                    user, getattr(cls, plan_attr), force=True)
                if state != SubscriptionState.ACTIVE:
                    Subscription.objects.filter(pk=subscription.pk).update(
                        state=state)
            cls.users[combo] = user


class EntitlementMatrixTests(MatrixTestCase):
    def test_every_plan_state_and_tier(self):
        for code in ("vault", "workflows", "cyprian", "aralia", "no-such-app"):
            for combo, _plan, _state in COMBOS:
                with self.subTest(code=code, combo=combo):
                    self.assertIs(
                        is_entitled(self.users[combo], code),
                        expected(code, combo))


class GateMatrixTests(MatrixTestCase):
    def _run(self, user, code, method, view=None, **extra):
        factory = RequestFactory()
        request = getattr(factory, method)(f"/{code}/x/", **extra)
        request.user = user
        request.resolver_match = type("M", (), {"app_name": code})()
        middleware = SubscriptionGateMiddleware(lambda r: None)
        return middleware.process_view(request, view, (), {}), request

    def test_a_brake_is_never_refused(self):
        """A write in a paid app that 402s unmarked passes when its view is
        marked @plan_exempt — and only that view."""
        user = self.users["never-subscribed"]
        response, _ = self._run(user, "aralia", "post", view=lambda request: None)
        self.assertEqual(response.status_code, 402)
        response, request = self._run(user, "aralia", "post",
                                      view=plan_exempt(lambda request: None))
        self.assertIsNone(response)
        self.assertFalse(request.plan_locked)

    def test_writes_402_exactly_where_the_plan_stops(self):
        for code in ("cyprian", "aralia"):
            for combo, _plan, _state in COMBOS:
                entitled = expected(code, combo)
                with self.subTest(code=code, combo=combo):
                    response, _ = self._run(self.users[combo], code, "post")
                    if entitled:
                        self.assertIsNone(response)
                    else:
                        self.assertEqual(response.status_code, 402)

    def test_reads_always_pass_and_are_marked(self):
        for code in ("cyprian", "aralia"):
            for combo, _plan, _state in COMBOS:
                entitled = expected(code, combo)
                with self.subTest(code=code, combo=combo):
                    response, request = self._run(
                        self.users[combo], code, "get")
                    self.assertIsNone(response)
                    self.assertIs(request.plan_locked, not entitled)

    def test_reads_are_refused_when_the_host_gates_reads(self):
        """SUBSCRIPTION_GATE_READS (1.51): outside the plan, not even a GET."""
        from django.test import override_settings

        with override_settings(SUBSCRIPTION_GATE_READS=True):
            for code in ("cyprian", "aralia"):
                for combo, _plan, _state in COMBOS:
                    entitled = expected(code, combo)
                    with self.subTest(code=code, combo=combo):
                        response, _ = self._run(self.users[combo], code, "get")
                        if entitled:
                            self.assertIsNone(response)
                        else:
                            self.assertEqual(response.status_code, 402)

    def test_enforcement_can_be_switched_off_while_installed(self):
        """SUBSCRIPTION_ENFORCEMENT=False (1.51): everything passes."""
        from django.test import override_settings

        with override_settings(SUBSCRIPTION_ENFORCEMENT=False, SUBSCRIPTION_GATE_READS=True):
            response, request = self._run(self.users["never-subscribed"], "aralia", "post")
        self.assertIsNone(response)
        self.assertFalse(request.plan_locked)

    def test_free_and_unknown_codes_are_never_touched(self):
        for code in ("vault", "workflows", "no-such-app", "assets"):
            for combo, _plan, _state in COMBOS:
                with self.subTest(code=code, combo=combo):
                    response, request = self._run(
                        self.users[combo], code, "post")
                    self.assertIsNone(response)
                    self.assertFalse(request.plan_locked)


class ApiWireTests(MatrixTestCase):
    """The refusal an API client sees: 402 JSON, machine-readable, every
    non-entitled combination."""

    def _post_json(self, user, code, **extra):
        factory = RequestFactory()
        request = factory.post(f"/{code}/api/x/", **extra)
        request.user = user
        request.resolver_match = type("M", (), {"app_name": code})()
        return SubscriptionGateMiddleware(
            lambda r: None).process_view(request, None, (), {})

    def test_every_denied_combo_answers_json_402(self):
        for code in ("cyprian", "aralia"):
            for combo, _plan, _state in COMBOS:
                if expected(code, combo):
                    continue
                with self.subTest(code=code, combo=combo):
                    response = self._post_json(
                        self.users[combo], code,
                        HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                    self.assertEqual(response.status_code, 402)
                    payload = json.loads(response.content)
                    self.assertEqual(payload["reason"],
                                     "subscription-required")
                    self.assertEqual(payload["entitlement"], code)
                    self.assertIn("plans_url", payload)

    def test_accept_header_and_content_type_also_get_json(self):
        user = self.users["never-subscribed"]
        for extra in ({"HTTP_ACCEPT": "application/json"},
                      {"content_type": "application/json", "data": "{}"}):
            with self.subTest(extra=extra):
                response = self._post_json(user, "aralia", **extra)
                self.assertEqual(response.status_code, 402)
                self.assertEqual(
                    json.loads(response.content)["entitlement"], "aralia")

    def test_a_browser_write_gets_the_persuasion_page(self):
        response = self._post_json(self.users["never-subscribed"], "cyprian")
        self.assertEqual(response.status_code, 402)
        self.assertIn(b"<", response.content[:20])


@modify_settings(MIDDLEWARE={"append": GATE})
class LiveEnforcementTests(MatrixTestCase):
    """End to end through the URL layer: a real gated app URL, the real
    middleware chain, the real 402 — what BUILD_SUBSCRIPTIONS_ENFORCE=1
    deploys."""

    #: The live path needs a real mounted URL whose app is NON-FREE in the
    #: catalogue and which answers both GET and POST — GET to prove reads pass,
    #: POST to prove the gate refuses. This was `kanban:api_project_list` until
    #: 2026-09-01, when zenobia retired the boards and every test here died with
    #: NoReverseMatch on a host that had simply stopped installing an app.
    #:
    #: `locations:api_address_list` is the same shape (AddressListCreateApiView
    #: — get + post) and a strictly better anchor: toto.locations is CORE, in
    #: the unconditional INSTALLED_APPS on every host, so no build decision can
    #: unmount it. Picking a retire-able app for a library test is the defect
    #: this replaces, not just the app that happened to be retired.
    #: Two URLs, not one, and the reason is a SECOND gate. Locations' API views
    #: are `MeshGatedApiView`: a GET from somebody outside the data mesh is 403
    #: before the view runs (toto/api/cors.py). That still proves "not 402", but
    #: it proves it without the read ever rendering — a test passing on a
    #: technicality. So the write half uses the API (POST is unaffected by the
    #: mesh gate) and the read half uses the ordinary page, which renders.
    LIVE_WRITE_URL = "locations:api_address_list"
    LIVE_READ_URL = "locations:locations_all"
    LIVE_APP = "locations"

    def setUp(self):
        # toto.locations left toto-base for toto-geo on 2026-10-04, so it is
        # no host's unconditional app any more: where it is not installed
        # these live checks have no anchor and are skipped.
        from django.apps import apps

        if not apps.is_installed("toto.locations"):
            self.skipTest("toto.locations is not installed: no live gated URL to ask")
        super().setUp()

    def _post(self, combo):
        from django.urls import reverse

        self.client.force_login(self.users[combo])
        return self.client.post(
            reverse(self.LIVE_WRITE_URL),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    def test_write_is_refused_by_plan(self):
        # The app is not in make_plans' entitlement lists, so it resolves via
        # the CATALOGUE (non-free there) — denied without a granting plan.
        from .catalogue import registry

        entitlement = registry.get(self.LIVE_APP)
        if entitlement is None or entitlement.free:
            self.skipTest(
                f"{self.LIVE_APP} is not a paid entitlement on this host")
        resp = self._post("never-subscribed")
        self.assertEqual(resp.status_code, 402)
        self.assertEqual(json.loads(resp.content)["entitlement"],
                         self.LIVE_APP)

    def test_reads_render_for_everyone(self):
        from django.urls import reverse

        self.client.force_login(self.users["never-subscribed"])
        resp = self.client.get(reverse(self.LIVE_READ_URL))
        self.assertNotEqual(resp.status_code, 402)
        # Stronger than "not 402": the page must actually render for somebody
        # who has never subscribed. That is the half a mesh-gated API URL
        # could not assert.
        self.assertEqual(resp.status_code, 200)
