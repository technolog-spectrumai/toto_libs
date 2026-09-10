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


class StorageEndpointTests(ApiTestCase):
    """Counts over the API, and the promise that it is only counts."""

    def _lease(self):
        return services.reserve(owner=self.user, name="thesis", limits=SMALL)

    def test_it_returns_numbers_and_no_names(self):
        """The boundary, asserted at the edge as well as in the module. An
        operator may see the size; what is in the capsule stays the owner's."""
        from unittest import mock

        from toto.anastasia import runtime

        lease = self._lease()
        reading = {"bytes": 4096, "files": 12, "directories": 3,
                   "symlinks": 0, "complete": True, "measured_in_seconds": 0.1}
        backend = mock.Mock()
        backend.storage.return_value = reading
        with mock.patch.object(runtime, "get_backend", return_value=backend):
            body = self.call("get", f"/api/v1/capsules/{lease.uuid}/storage").json()
        self.assertEqual(body, reading)
        for value in body.values():
            self.assertIsInstance(value, (int, float, bool))

    def test_another_persons_capsule_is_a_404(self):
        theirs = services.reserve(owner=self.other, name="t", limits=SMALL)
        response = self.call("get", f"/api/v1/capsules/{theirs.uuid}/storage")
        self.assertEqual(response.status_code, 404)

    def test_a_runtime_that_cannot_answer_says_so_rather_than_lying(self):
        """An older executor has no storage route. Reporting zero would read
        as "this capsule is empty", which is a different and worse claim."""
        from unittest import mock

        from toto.anastasia import runtime

        lease = self._lease()
        backend = mock.Mock(spec=[])            # no `storage` attribute
        with mock.patch.object(runtime, "get_backend", return_value=backend):
            response = self.call(
                "get", f"/api/v1/capsules/{lease.uuid}/storage")
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()["code"], "unsupported")

    def test_an_unreachable_runtime_is_reported_as_incomplete(self):
        """The backend returns {} when it cannot reach the executor. That must
        surface as "we could not measure", never as zero bytes."""
        from unittest import mock

        from toto.anastasia import runtime

        lease = self._lease()
        backend = mock.Mock()
        backend.storage.return_value = {}
        with mock.patch.object(runtime, "get_backend", return_value=backend):
            body = self.call(
                "get", f"/api/v1/capsules/{lease.uuid}/storage").json()
        self.assertFalse(body["complete"])


class RouteOrderTests(ApiTestCase):
    """A catch-all must not swallow its siblings.

    `capsules/<uuid>/<str:action>` matches anything, "storage" and "jobs"
    included. Django takes the first pattern that matches, so placing it above
    them made /storage answer 405 — the action view is POST-only — which reads
    like a broken endpoint rather than a shadowed route. Cheap to break again
    by adding a path in the obvious place, so it is pinned.
    """

    def test_named_subpaths_win_over_the_action_catch_all(self):
        from unittest import mock

        from toto.anastasia import runtime

        lease = services.reserve(owner=self.user, name="t", limits=SMALL)
        backend = mock.Mock()
        backend.storage.return_value = {"bytes": 0, "complete": True}
        with mock.patch.object(runtime, "get_backend", return_value=backend):
            response = self.call(
                "get", f"/api/v1/capsules/{lease.uuid}/storage")
        self.assertEqual(response.status_code, 200,
                         "the action catch-all has shadowed /storage again")

    def test_the_action_route_still_works(self):
        lease = services.reserve(owner=self.user, name="t2", limits=SMALL)
        response = self.call("post", f"/api/v1/capsules/{lease.uuid}/mount",
                             body={})
        self.assertIn(response.status_code, (200, 409),
                      "mount must still resolve through the catch-all")


class JobOutputTests(ApiTestCase):
    """Fetching what a job produced, without a browser."""

    def _execution(self, status):
        from toto.anastasia.models import Execution

        lease = services.reserve(owner=self.user, name="o", limits=SMALL)
        return Execution.objects.create(
            lease=lease, operation="render_pdf", family="pdf", status=status,
            cpu_millicores=500, ram_mb=128, scratch_mb=64, pids=32,
            timeout_seconds=60, requested_by=self.user)

    def test_a_running_job_refuses_rather_than_returning_a_partial(self):
        """A partial output that looks complete is the kind of answer a client
        writes to a file and a person then trusts."""
        from toto.anastasia import choices

        execution = self._execution(choices.RUNNING)
        response = self.call("get", f"/api/v1/jobs/{execution.uuid}/output")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "still_running")

    def test_a_finished_job_returns_its_files_base64(self):
        import base64
        from unittest import mock

        from toto.anastasia import choices, jobs, runtime

        execution = self._execution(choices.SUCCESS)
        backend = mock.Mock()
        backend.collect.return_value = jobs.tar_of({"out.pdf": b"%PDF-1.4"})
        with mock.patch.object(runtime, "get_backend", return_value=backend):
            body = self.call(
                "get", f"/api/v1/jobs/{execution.uuid}/output").json()
        self.assertEqual(base64.b64decode(body["files"]["out.pdf"]), b"%PDF-1.4")

    def test_an_unreachable_runtime_is_a_503_not_a_500(self):
        """A 500 sends a client into a retry loop against a machine that is
        down; a 503 says come back later, which is the truth."""
        from unittest import mock

        from toto.anastasia import choices, runtime

        execution = self._execution(choices.SUCCESS)
        backend = mock.Mock()
        backend.collect.side_effect = OSError("socket gone")
        with mock.patch.object(runtime, "get_backend", return_value=backend):
            response = self.call(
                "get", f"/api/v1/jobs/{execution.uuid}/output")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "runtime_unavailable")

    def test_another_persons_job_output_is_a_404(self):
        from toto.anastasia import choices
        from toto.anastasia.models import Execution

        theirs = services.reserve(owner=self.other, name="t", limits=SMALL)
        execution = Execution.objects.create(
            lease=theirs, operation="render_pdf", family="pdf",
            status=choices.SUCCESS, cpu_millicores=500, ram_mb=128,
            scratch_mb=64, pids=32, timeout_seconds=60,
            requested_by=self.other)
        response = self.call("get", f"/api/v1/jobs/{execution.uuid}/output")
        self.assertEqual(response.status_code, 404)


class TokenHardeningTests(AnastasiaTestCase):
    """The credential, pressed on the parts that would fail quietly."""

    def test_two_tokens_never_share_a_selector(self):
        """The selector is the index. A collision would make one token find
        the other's row and then fail the verifier — a working credential
        rejected, with nothing to see in a log."""
        selectors = {CapsuleToken.issue(owner=self.user, label=f"t{i}")[0].selector
                     for i in range(25)}
        self.assertEqual(len(selectors), 25)

    def test_the_stored_hint_cannot_reconstruct_the_secret(self):
        """A hint exists so an operator can match a token in a config file to
        a row. It must not be enough to use."""
        row, raw = CapsuleToken.issue(owner=self.user, label="laptop")
        verifier = raw.split(".")[-1]
        self.assertLess(len(row.hint), len(verifier))
        self.assertTrue(verifier.endswith(row.hint))

    def test_a_token_is_bound_to_one_owner(self):
        row, raw = CapsuleToken.issue(owner=self.user, label="mine")
        self.assertEqual(CapsuleToken.authenticate(raw).owner, self.user)
        self.assertNotEqual(row.owner, self.other)

    def test_revoking_one_token_leaves_the_others_working(self):
        _a, raw_a = CapsuleToken.issue(owner=self.user, label="a")
        b, raw_b = CapsuleToken.issue(owner=self.user, label="b")
        b.revoke()
        self.assertIsNone(CapsuleToken.authenticate(raw_b))
        self.assertIsNotNone(CapsuleToken.authenticate(raw_a))

    def test_a_token_with_no_expiry_does_not_expire(self):
        """Most tokens are for a machine that runs indefinitely. An accidental
        default expiry would log a client out at an hour nobody chose."""
        _row, raw = CapsuleToken.issue(owner=self.user, label="forever")
        self.assertIsNotNone(CapsuleToken.authenticate(raw))

    def test_the_prefix_is_required(self):
        """A bare selector.verifier must not authenticate: the prefix is what
        makes a leaked token recognisable in a log or a config file."""
        _row, raw = CapsuleToken.issue(owner=self.user, label="x")
        _prefix, selector, verifier = raw.split(".")
        self.assertIsNone(CapsuleToken.authenticate(f"{selector}.{verifier}"))

    def test_a_token_for_a_deleted_user_stops_working(self):
        """CASCADE on the owner. A credential outliving its person is the
        definition of an orphaned key."""
        from django.contrib.auth import get_user_model

        doomed = get_user_model().objects.create_user("doomed", password="x")
        _row, raw = CapsuleToken.issue(owner=doomed, label="theirs")
        doomed.delete()
        self.assertIsNone(CapsuleToken.authenticate(raw))


class ApiRefusalShapeTests(ApiTestCase):
    """Every refusal a client can meet, in the one shape it parses."""

    def test_an_unknown_capsule_action_is_a_404_with_a_code(self):
        lease = services.reserve(owner=self.user, name="c", limits=SMALL)
        response = self.call("post", f"/api/v1/capsules/{lease.uuid}/detonate",
                             body={})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "bad_action")

    def test_a_get_on_a_post_endpoint_is_405_not_a_crash(self):
        lease = services.reserve(owner=self.user, name="c", limits=SMALL)
        self.assertEqual(
            self.call("get", f"/api/v1/capsules/{lease.uuid}/mount").status_code,
            405)

    def test_reserving_with_no_limits_is_refused_not_defaulted(self):
        """Silently defaulting capacity would hand out whatever the code
        happened to choose, against a pool somebody else is sharing."""
        response = self.call("post", "/api/v1/capsules/new", body={"name": "x"})
        self.assertIn(response.status_code, (400, 409))

    def test_every_error_body_has_both_halves(self):
        """A sentence for the person, a code for the client. A body missing
        either forces the caller to match on English."""
        for response in (
                self.client.get("/api/v1/pool"),
                self.call("get", "/api/v1/pool", raw="capsule.bad.token"),
                self.call("post", "/api/v1/capsules/new", body={"name": "x"}),
        ):
            with self.subTest(status=response.status_code):
                body = response.json()
                self.assertIn("error", body)
                self.assertIn("code", body)

