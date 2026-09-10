"""The client API: authentication, ownership, and a frozen contract.

Unit-level on purpose. Nothing here starts a container — these assert what the
API *promises*, which is the half a client in another repository depends on and
the half that breaks silently when somebody refactors in here.
"""

from __future__ import annotations

import json

from django.test import override_settings
from django.utils import timezone

from toto.anastasia import services
from toto.anastasia.limits import Limits
from toto.anastasia.tokens import CapsuleToken

from .base import SMALL, AnastasiaTestCase


class ApiTestCase(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        self.token_row, self.raw = CapsuleToken.issue(
            owner=self.user, label="zinnia on the laptop")

    def call(self, method, path, *, body=None, raw=None):
        kwargs = {"HTTP_AUTHORIZATION": f"Bearer {raw or self.raw}"}
        if body is not None:
            kwargs["content_type"] = "application/json"
            return getattr(self.client, method)(path, json.dumps(body), **kwargs)
        return getattr(self.client, method)(path, **kwargs)


class TokenTests(AnastasiaTestCase):
    """The credential itself."""

    def test_the_secret_is_returned_once_and_never_stored(self):
        row, raw = CapsuleToken.issue(owner=self.user, label="laptop")
        self.assertNotIn(raw.split(".")[-1], row.verifier_hash,
                         "the verifier must be hashed, not kept")
        self.assertEqual(CapsuleToken.objects.get(pk=row.pk).verifier_hash,
                         row.verifier_hash)

    def test_a_token_authenticates_itself(self):
        row, raw = CapsuleToken.issue(owner=self.user, label="laptop")
        self.assertEqual(CapsuleToken.authenticate(raw), row)

    def test_rubbish_is_refused_without_touching_the_database(self):
        """Cheap refusals first. A malformed token must not reach the hash
        comparison — that is the ~100ms-per-attempt amplifier the SSO provider
        once saturated on."""
        for bad in ("", "nonsense", "capsule.only-two", "wrong.a.b",
                    "capsule..", "capsule.x.y"):
            with self.subTest(token=bad):
                self.assertIsNone(CapsuleToken.authenticate(bad))

    def test_a_revoked_token_stops_working(self):
        row, raw = CapsuleToken.issue(owner=self.user, label="laptop")
        row.revoke()
        self.assertIsNone(CapsuleToken.authenticate(raw))

    def test_an_expired_token_stops_working(self):
        past = timezone.now() - timezone.timedelta(minutes=1)
        _row, raw = CapsuleToken.issue(owner=self.user, label="old",
                                       expires_at=past)
        self.assertIsNone(CapsuleToken.authenticate(raw))

    def test_the_verifier_of_one_token_does_not_open_another(self):
        """The selector picks the row; the verifier must still be checked
        against THAT row. Swapping halves is the obvious attack."""
        _a, raw_a = CapsuleToken.issue(owner=self.user, label="a")
        _b, raw_b = CapsuleToken.issue(owner=self.user, label="b")
        prefix, sel_a, _ver_a = raw_a.split(".")
        _p, _s, ver_b = raw_b.split(".")
        self.assertIsNone(CapsuleToken.authenticate(f"{prefix}.{sel_a}.{ver_b}"))

    def test_use_is_recorded_but_not_on_every_request(self):
        """A client polls job status. A write per poll turns a read endpoint
        into a write-heavy one for a number nobody reads that finely."""
        row, _raw = CapsuleToken.issue(owner=self.user, label="laptop")
        row.touch()
        first = CapsuleToken.objects.get(pk=row.pk).last_used_at
        self.assertIsNotNone(first)
        row.touch()
        self.assertEqual(CapsuleToken.objects.get(pk=row.pk).last_used_at, first)


class AuthenticationTests(ApiTestCase):
    def test_no_token_is_a_401_that_says_how(self):
        response = self.client.get("/api/v1/pool")
        self.assertEqual(response.status_code, 401)
        body = response.json()
        self.assertEqual(body["code"], "no_token")
        self.assertIn("Bearer", body["error"])

    def test_a_bad_token_is_a_401(self):
        response = self.call("get", "/api/v1/pool", raw="capsule.no.such")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "bad_token")

    def test_a_bearer_call_needs_no_session_and_no_csrf(self):
        """THE POINT OF THE WHOLE STAGE. A desktop client has no cookie jar.
        If this ever needs a session, the client cannot work at all."""
        self.assertNotIn("sessionid", self.client.cookies)
        self.assertEqual(self.call("get", "/api/v1/pool").status_code, 200)

    def test_a_post_needs_no_csrf_token(self):
        response = self.call("post", "/api/v1/capsules/new",
                             body={"name": "x", "limits": SMALL.as_dict()})
        self.assertNotEqual(response.status_code, 403,
                            "CSRF must not apply to a bearer-authenticated API")


class OwnershipTests(ApiTestCase):
    def test_another_persons_capsule_is_a_404_not_a_403(self):
        """A 403 would confirm it exists, which the owner did not agree to."""
        theirs = services.reserve(owner=self.other, name="theirs", limits=SMALL)
        response = self.call("get", f"/api/v1/capsules/{theirs.uuid}")
        self.assertEqual(response.status_code, 404)

    def test_a_token_only_lists_its_owners_capsules(self):
        services.reserve(owner=self.user, name="mine", limits=SMALL)
        services.reserve(owner=self.other, name="theirs", limits=SMALL)
        body = self.call("get", "/api/v1/capsules").json()
        self.assertEqual([c["name"] for c in body["capsules"]], ["mine"])

    def test_another_persons_job_is_a_404(self):
        from toto.anastasia.models import Execution

        theirs = services.reserve(owner=self.other, name="t", limits=SMALL)
        execution = Execution.objects.create(
            lease=theirs, operation="render_pdf", family="pdf",
            cpu_millicores=500, ram_mb=128, scratch_mb=64, pids=32,
            timeout_seconds=60, requested_by=self.other)
        response = self.call("get", f"/api/v1/jobs/{execution.uuid}")
        self.assertEqual(response.status_code, 404)


class ContractTests(ApiTestCase):
    """FROZEN SHAPES.

    A client lives in another repository and is deployed on its own schedule,
    so a key renamed here is a client broken there — and the failure surfaces
    in the other repo, days later, as "the app stopped working".

    Adding a key is fine and these tests allow it. Renaming or removing one
    must fail HERE. That is the whole purpose; do not "fix" a failure by
    editing the expected set.
    """

    def assertHasKeys(self, body, keys, what):
        missing = sorted(set(keys) - set(body))
        self.assertEqual(
            missing, [],
            f"{what} lost {missing} — a client in zinnia reads these. If the "
            "change is deliberate, add /api/v2 rather than editing this test.")

    def test_the_pool_shape_is_frozen(self):
        body = self.call("get", "/api/v1/pool").json()
        self.assertHasKeys(body, {"configured", "total", "booked", "available",
                                  "capsules_open"}, "the pool report")

    def test_the_capsule_shape_is_frozen(self):
        lease = services.reserve(owner=self.user, name="thesis", limits=SMALL)
        body = self.call("get", f"/api/v1/capsules/{lease.uuid}").json()
        self.assertHasKeys(body, {
            "uuid", "name", "state", "detail", "reserved", "free", "usage",
            "sample_age_seconds", "expires_at", "executions_running",
            "accepts_work", "tier", "isolates_kernel"}, "the capsule report")

    def test_isolation_is_a_boolean_beside_the_tier_not_only_a_string(self):
        """A client that interpolated `tier` into "your code runs in a {tier}"
        is exactly how a container gets described as a virtual machine. The
        boolean is what a client is meant to branch on."""
        lease = services.reserve(owner=self.user, name="t", limits=SMALL)
        body = self.call("get", f"/api/v1/capsules/{lease.uuid}").json()
        self.assertIsInstance(body["isolates_kernel"], bool)

    def test_a_refusal_carries_a_code_a_client_can_branch_on(self):
        """The sentence is for a person; the code is for the client. A client
        that had to match on English would break on a reworded message."""
        huge = Limits(cpu_millicores=10 ** 9, ram_mb=10 ** 9,
                      scratch_mb=10 ** 9, pids=10 ** 6)
        response = self.call("post", "/api/v1/capsules/new",
                             body={"name": "toobig", "limits": huge.as_dict()})
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertTrue(body["code"], "a refusal with no code is unbranchable")
        self.assertTrue(body["error"])

    def test_malformed_limits_are_a_400_not_a_500(self):
        response = self.call("post", "/api/v1/capsules/new",
                             body={"name": "x", "limits": {"cpu_millicores": "lots"}})
        self.assertEqual(response.status_code, 400)

    def test_a_body_that_is_not_json_is_a_refusal_not_a_crash(self):
        response = self.client.post(
            "/api/v1/capsules/new", "not json at all",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw}")
        self.assertIn(response.status_code, (400, 409))
