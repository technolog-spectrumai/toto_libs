"""The client API: authentication, ownership, and a frozen contract.

Unit-level on purpose. Nothing here starts a container — these assert what the
API *promises*, which is the half a client in another repository depends on and
the half that breaks silently when somebody refactors in here.
"""

from __future__ import annotations

import json
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from toto.anastasia import services
from toto.anastasia.limits import Limits
from toto.anastasia.tokens import CapsuleToken

from .base import SMALL, AnastasiaTestCase, FakeRuntimeBackend


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

    def test_a_token_dies_with_its_owner(self):
        """CASCADE on the owner. A credential outliving its person is the
        definition of an orphaned key.

        ASSERTED ON THE DECLARATION, NOT BY DELETING A USER, and that is not
        timidity. `toto.quota.tests` defines a test-only model with a foreign
        key to User; Django's cascade collector walks every such model, so an
        actual `user.delete()` here raises `no such table:
        quota_sampleusageevent` whenever this module runs beside quota's — as
        it does in the gate, and only there. The first version of this test
        passed alone and failed the gate for a reason that had nothing to do
        with tokens.

        The declaration is what the guarantee actually rests on, and checking
        it costs no database at all.
        """
        from django.db.models import CASCADE

        field = CapsuleToken._meta.get_field("owner")
        self.assertIs(field.remote_field.on_delete, CASCADE)


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



class MeTests(ApiTestCase):
    """The first call a client makes."""

    def test_it_names_the_person_and_the_token(self):
        body = self.call("get", "/api/v1/me").json()
        self.assertEqual(body["username"], self.user.get_username())
        self.assertEqual(body["token"]["label"], "zinnia on the laptop")

    def test_it_returns_nothing_secret(self):
        """`hint` is six characters; the SELECTOR is half the credential and is
        absent even though the server stores it in the clear."""
        selector = self.raw.split(".")[1]
        verifier = self.raw.split(".")[2]
        text = self.call("get", "/api/v1/me").content.decode()
        self.assertNotIn(selector, text)
        self.assertNotIn(verifier, text)
        self.assertNotIn(self.raw, text)

    def test_it_proves_the_token_in_one_round_trip(self):
        """A dead token must fail HERE, so a connect screen never shows a
        signed-in state it would lose on the next call."""
        self.token_row.revoke()
        self.assertEqual(self.call("get", "/api/v1/me").status_code, 401)

    def test_it_needs_a_token(self):
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)

    def test_it_reports_staffness_so_a_client_need_not_infer_it(self):
        body = self.call("get", "/api/v1/me").json()
        self.assertIs(body["is_staff"], self.user.is_staff)


class TokenDeskTests(AnastasiaTestCase):
    """HOW A PERSON GETS A TOKEN, which nothing answered until 2026-09-10.

    `CapsuleToken.issue()` had no caller but this file: no route, no admin
    registration, no management command. Every endpoint above authenticated
    with a credential a user had no way to obtain, so the whole API was
    reachable in principle and unusable in practice — and every individual
    piece looked finished, which is why it survived a full campaign.

    `test_the_api_is_reachable_end_to_end` is the one that would have caught
    it: it mints through the page and then CALLS the API with what the page
    printed. Every other test here mints in Python, which is exactly the blind
    spot that let this ship.
    """

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def _mint(self, label="laptop"):
        return self.client.post("/capsules/tokens/new/", {"label": label},
                                follow=True)

    # -- the gap itself ----------------------------------------------------

    def test_the_api_is_reachable_end_to_end(self):
        """Mint through the PAGE, then use what the page showed. No shortcuts."""
        page = self._mint("zinnia")
        raw = page.context["fresh_token"]
        self.assertTrue(raw.startswith("capsule."), raw[:20])

        answer = self.client.get("/api/v1/pool", HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(answer.status_code, 200, answer.content)
        self.assertIn("total", answer.json())

    def test_the_desk_links_to_the_token_page(self):
        """An undiscoverable page is the same defect one step later."""
        body = self.client.get("/capsules/").content.decode()
        self.assertIn("/capsules/tokens/", body)

    # -- showing the secret ------------------------------------------------

    def test_the_secret_is_shown_once_and_not_on_a_reload(self):
        """It is popped from the session, so a refresh cannot re-reveal it —
        and a shoulder-surfer reading the screen later sees nothing."""
        self.assertTrue(self._mint().context["fresh_token"])
        again = self.client.get("/capsules/tokens/")
        self.assertEqual(again.context["fresh_token"], "")

    def test_the_secret_never_reaches_the_url(self):
        """A query string lands in history, in the access log and in every
        proxy between. The redirect target must be bare."""
        response = self.client.post("/capsules/tokens/new/", {"label": "laptop"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/capsules/tokens/")

    def test_the_page_never_shows_a_secret_it_did_not_just_mint(self):
        """The hint is 6 characters and the stored half is a hash; neither is
        the credential. Asserted against the RAW body, because a template that
        rendered `token.selector` would be handing out half the secret."""
        _row, raw = CapsuleToken.issue(owner=self.user, label="elsewhere")
        selector = raw.split(".")[1]
        body = self.client.get("/capsules/tokens/").content.decode()
        self.assertIn("elsewhere", body)          # the row is listed
        self.assertNotIn(selector, body)          # its selector is not
        self.assertNotIn(raw, body)

    # -- ownership ---------------------------------------------------------

    def test_only_your_own_tokens_are_listed(self):
        CapsuleToken.issue(owner=self.other, label="not yours")
        CapsuleToken.issue(owner=self.user, label="mine")
        body = self.client.get("/capsules/tokens/").content.decode()
        self.assertIn("mine", body)
        self.assertNotIn("not yours", body)

    def test_revoking_somebody_elses_token_is_a_404(self):
        theirs, raw = CapsuleToken.issue(owner=self.other, label="theirs")
        response = self.client.post(f"/capsules/tokens/{theirs.pk}/revoke/")
        self.assertEqual(response.status_code, 404)
        # And it still works, which is the half a 404-not-403 test usually
        # forgets: refusing to say it exists must not half-revoke it.
        self.assertIsNotNone(CapsuleToken.authenticate(raw))

    def test_an_anonymous_visitor_cannot_mint(self):
        """Sent to login, and NOTHING minted.

        The redirect target legitimately contains the token path — it is the
        `?next=` — so asserting on the URL is the wrong test. What matters is
        that the row does not exist: a refusal that still writes is not a
        refusal.
        """
        self.client.logout()
        response = self.client.post("/capsules/tokens/new/", {"label": "x"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])
        self.assertEqual(CapsuleToken.objects.count(), 0)

    # -- revoking ----------------------------------------------------------

    def test_revoking_stops_the_api_at_once(self):
        raw = self._mint("doomed").context["fresh_token"]
        row = CapsuleToken.objects.get(label="doomed")
        self.client.post(f"/capsules/tokens/{row.pk}/revoke/")

        answer = self.client.get("/api/v1/pool",
                                 HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(answer.status_code, 401)
        self.assertEqual(answer.json()["code"], "bad_token")

    def test_a_revoked_row_is_still_listed(self):
        """"I revoked that yesterday" is a thing a person needs to confirm,
        and a row that vanishes cannot confirm it."""
        row, _raw = CapsuleToken.issue(owner=self.user, label="retired")
        row.revoke()
        self.assertIn("retired",
                      self.client.get("/capsules/tokens/").content.decode())

    def test_revoking_twice_is_harmless(self):
        row, _raw = CapsuleToken.issue(owner=self.user, label="twice")
        first = self.client.post(f"/capsules/tokens/{row.pk}/revoke/")
        self.assertEqual(first.status_code, 302)
        row.refresh_from_db()
        stamp = row.revoked_at
        self.client.post(f"/capsules/tokens/{row.pk}/revoke/")
        row.refresh_from_db()
        self.assertEqual(row.revoked_at, stamp, "the second revoke moved the date")

    # -- refusals ----------------------------------------------------------

    def test_a_token_needs_a_label(self):
        """The label is what somebody reads when deciding which row to revoke.
        An unlabelled one is a row nobody dares touch."""
        before = CapsuleToken.objects.count()
        self._mint("   ")
        self.assertEqual(CapsuleToken.objects.count(), before)

    def test_the_number_of_live_tokens_is_capped(self):
        from toto.anastasia import views

        for i in range(views.MAX_TOKENS_PER_USER):
            CapsuleToken.issue(owner=self.user, label=f"t{i}")
        self._mint("one too many")
        self.assertEqual(
            CapsuleToken.objects.filter(owner=self.user).count(),
            views.MAX_TOKENS_PER_USER)

    def test_revoking_frees_a_slot(self):
        """The cap counts LIVE tokens, so it cannot become a permanent lockout
        for anyone who has ever minted ten."""
        from toto.anastasia import views

        rows = [CapsuleToken.issue(owner=self.user, label=f"t{i}")[0]
                for i in range(views.MAX_TOKENS_PER_USER)]
        rows[0].revoke()
        self._mint("replacement")
        self.assertTrue(
            CapsuleToken.objects.filter(owner=self.user,
                                        label="replacement").exists())

    def test_the_pages_refuse_a_GET_where_they_write(self):
        for path in ("/capsules/tokens/new/",):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 405)


class JobInputTests(ApiTestCase):
    """A job you can actually give work to.

    Until 2026-09-10 `job_create` accepted `operation` and `params` and nothing
    else, so a client could start `compile_latex` or `run_python` and had no
    way to say WHAT to compile or run. Every family this API reaches needs a
    staged file. The desk's own callers never noticed because they call
    `jobs.run` in-process and pass `inputs=` there — the API was the only door
    without one.
    """

    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=Limits(3000, 6144, 6144, 768))
        services.mount(lease=self.lease, actor=self.user)
        FakeRuntimeBackend.reset()

    def _submit(self, **body):
        return self.call("post", f"/api/v1/capsules/{self.lease.uuid}/jobs",
                         body={"operation": "run_python", **body})

    def test_a_script_reaches_the_runtime(self):
        import base64

        from toto.anastasia import jobs as jobs_mod

        seen = {}
        original = jobs_mod.tar_of

        def spy(files):
            seen.update(files)
            return original(files)

        with mock.patch.object(jobs_mod, "tar_of", spy):
            response = self._submit(
                params={"script": "main.py"},
                inputs={"main.py": base64.b64encode(b"print(1)").decode()})

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(seen, {"main.py": b"print(1)"})

    def test_a_job_with_no_inputs_still_works(self):
        """`render_pdf` needs one, but the field is optional at this layer —
        the operation's own parameter rules are what refuse a missing file."""
        self.assertEqual(self._submit().status_code, 201)

    def test_the_timeout_travels(self):
        response = self._submit(timeout=90)
        self.assertEqual(response.status_code, 201, response.content)
        from toto.anastasia.models import Execution
        self.assertEqual(Execution.objects.latest("created_at").timeout_seconds, 90)

    # -- refusals, each naming the file --------------------------------------

    def test_a_path_is_refused_by_name(self):
        import base64

        payload = base64.b64encode(b"x").decode()
        for bad in ("../escape.py", "/etc/passwd", "a/../../b.py"):
            with self.subTest(name=bad):
                response = self._submit(inputs={bad: payload})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "bad_input_name")
                self.assertIn(bad, response.json()["error"])

    def test_a_nested_name_is_allowed(self):
        """`sub/main.tex` is an ordinary thing in a LaTeX project. Only
        traversal and absolute paths are refused, not directories."""
        import base64

        response = self._submit(
            inputs={"sub/main.tex": base64.b64encode(b"x").decode()})
        self.assertEqual(response.status_code, 201, response.content)

    def test_bad_base64_is_a_refusal_not_a_500(self):
        response = self._submit(inputs={"main.py": "not base64!!"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "bad_inputs")
        self.assertIn("main.py", response.json()["error"])

    def test_inputs_must_be_a_mapping(self):
        response = self._submit(inputs=["main.py"])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "bad_inputs")

    def test_too_many_files_is_refused(self):
        import base64

        from toto.anastasia import api

        payload = base64.b64encode(b"x").decode()
        many = {f"f{i}.txt": payload for i in range(api.MAX_INPUT_FILES + 1)}
        response = self._submit(inputs=many)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "too_many_inputs")

    def test_an_oversized_body_is_413_not_a_memory_problem(self):
        """The ceiling is on the WEB tier, which decodes into memory to build
        the tar — the executor's own tar guard does not protect this process."""
        import base64

        from toto.anastasia import api

        with mock.patch.object(api, "MAX_INPUT_BYTES", 16):
            response = self._submit(
                inputs={"big.py": base64.b64encode(b"x" * 64).decode()})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "inputs_too_large")

    def test_nothing_is_submitted_when_the_inputs_are_refused(self):
        """A refusal that still books a job is not a refusal."""
        from toto.anastasia.models import Execution

        before = Execution.objects.count()
        self._submit(inputs={"../x.py": "eA=="})
        self.assertEqual(Execution.objects.count(), before)


class JobLogTests(ApiTestCase):
    """Watching a job, as distinct from asking whether it is done."""

    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=Limits(3000, 6144, 6144, 768))
        services.mount(lease=self.lease, actor=self.user)
        FakeRuntimeBackend.reset()
        response = self.call("post", f"/api/v1/capsules/{self.lease.uuid}/jobs",
                             body={"operation": "run_python"})
        self.job = response.json()["uuid"]

    def _logs(self, query=""):
        return self.call("get", f"/api/v1/jobs/{self.job}/logs{query}")

    def test_it_answers_while_the_job_is_still_running(self):
        """The whole point. `job_output` refuses until finished; this must
        not, or there is nothing to watch."""
        FakeRuntimeBackend.log_text = "compiling…\n"
        body = self._logs().json()
        self.assertEqual(body["text"], "compiling…\n")
        self.assertFalse(body["finished"])

    def test_the_offset_advances_and_does_not_repeat(self):
        FakeRuntimeBackend.log_text = "one\n"
        first = self._logs().json()
        self.assertEqual(first["text"], "one\n")

        FakeRuntimeBackend.log_text = "one\ntwo\n"
        second = self._logs(f"?offset={first['offset']}").json()
        self.assertEqual(second["text"], "two\n",
                         "the second slice repeated output the client had")
        self.assertGreater(second["offset"], first["offset"])

    def test_the_offset_is_a_byte_count_not_a_character_count(self):
        """A multi-byte line would desynchronise a character-based cursor and
        the next slice would start mid-codepoint."""
        FakeRuntimeBackend.log_text = "zażółć\n"
        body = self._logs().json()
        self.assertEqual(body["offset"], len("zażółć\n".encode("utf-8")))

    def test_a_junk_offset_starts_from_the_beginning(self):
        """It arrives from a query string, so "abc" is a typo — and the
        endpoint whose job is to show what went wrong is the worst place to
        answer a typo with a stack trace."""
        FakeRuntimeBackend.log_text = "hello\n"
        for junk in ("?offset=abc", "?offset=", "?offset=-5", "?offset=1e9999"):
            with self.subTest(query=junk):
                response = self._logs(junk)
                self.assertEqual(response.status_code, 200)

    def test_an_unreachable_runtime_is_an_empty_slice_not_a_500(self):
        """A progress console that 500s is worse than one showing nothing new,
        and the client polls again in a second either way."""
        FakeRuntimeBackend.fail_logs = True
        body = self._logs("?offset=7").json()
        self.assertEqual(body["text"], "")
        self.assertFalse(body["complete"])
        self.assertEqual(body["offset"], 7,
                         "the cursor was lost, so the client restarts the log")

    def test_somebody_elses_job_is_a_404(self):
        _theirs, raw = CapsuleToken.issue(owner=self.other, label="theirs")
        response = self.call("get", f"/api/v1/jobs/{self.job}/logs", raw=raw)
        self.assertEqual(response.status_code, 404)

    def test_it_needs_a_token(self):
        self.assertEqual(
            self.client.get(f"/api/v1/jobs/{self.job}/logs").status_code, 401)

    def test_a_runtime_with_no_log_reader_says_so_rather_than_crashing(self):
        """`NullRuntimeBackend` and any older backend have no `execution_logs`;
        a 501 names the deployment's limit instead of raising AttributeError."""
        with mock.patch("toto.anastasia.runtime.get_backend",
                        return_value=mock.Mock(spec=["mount"])):
            response = self._logs()
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()["code"], "unsupported")


class InputRoundTripTests(ApiTestCase):
    """What a client stages is what the runner receives.

    The client base64-encodes; the server decodes and tars; the executor
    untars. Three hops, and the one that silently corrupts is encoding — a
    naive `btoa` mangles anything outside Latin-1 and nothing raises.
    """

    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=Limits(3000, 6144, 6144, 768))
        services.mount(lease=self.lease, actor=self.user)
        FakeRuntimeBackend.reset()

    def test_utf8_survives_the_encode_decode_tar_round_trip(self):
        """A Polish comment or an emoji in a script must arrive byte-identical.
        This is the hop where a naive base64 mangles it silently."""
        import base64

        from toto.anastasia import jobs as jobs_mod

        source = "# zażółć gęślą jaźń\nprint('🐍')\n".encode("utf-8")
        seen = {}
        original = jobs_mod.tar_of

        def spy(files):
            seen.update(files)
            return original(files)

        with mock.patch.object(jobs_mod, "tar_of", spy):
            response = self.call(
                "post", f"/api/v1/capsules/{self.lease.uuid}/jobs",
                body={"operation": "run_python",
                      "inputs": {"main.py": base64.b64encode(source).decode()}})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(seen["main.py"], source)
        self.assertEqual(seen["main.py"].decode("utf-8"),
                         "# zażółć gęślą jaźń\nprint('🐍')\n")

    def test_binary_content_is_not_mangled(self):
        """A `.png` a script needs, or a `.sty`. Base64 carries bytes, and
        nothing on this path may assume text."""
        import base64

        from toto.anastasia import jobs as jobs_mod

        blob = bytes(range(256))
        seen = {}
        with mock.patch.object(jobs_mod, "tar_of",
                               lambda files: seen.update(files) or b""):
            self.call("post", f"/api/v1/capsules/{self.lease.uuid}/jobs",
                      body={"operation": "run_python",
                            "inputs": {"logo.png": base64.b64encode(blob).decode()}})
        self.assertEqual(seen["logo.png"], blob)

    def test_an_empty_file_is_staged_rather_than_dropped(self):
        """An empty `__init__.py` is a real thing a Python project needs."""
        from toto.anastasia import jobs as jobs_mod

        seen = {}
        with mock.patch.object(jobs_mod, "tar_of",
                               lambda files: seen.update(files) or b""):
            self.call("post", f"/api/v1/capsules/{self.lease.uuid}/jobs",
                      body={"operation": "run_python",
                            "inputs": {"__init__.py": ""}})
        self.assertIn("__init__.py", seen)
        self.assertEqual(seen["__init__.py"], b"")

    def test_the_tar_a_client_sends_is_readable_by_the_runner_helper(self):
        """End to end through the real `tar_of`/`files_from` pair, which is
        what the executor untars with. A tar this pair cannot round-trip is one
        the runner would find empty."""
        from toto.anastasia import jobs as jobs_mod

        files = {"main.py": "print('hi')\n".encode("utf-8"),
                 "sub/helper.py": b"x = 1\n"}
        self.assertEqual(jobs_mod.files_from(jobs_mod.tar_of(files)), files)
