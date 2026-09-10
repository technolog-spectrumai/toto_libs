"""The workspace API: authentication, ownership, and a frozen contract.

Unit-level on purpose. Nothing here starts a container — these assert what the
API *promises*, which is the half a client in another repository depends on and
the half that breaks silently when somebody refactors in here.

THE TEST THAT MATTERS MOST is `MeteringParityTests`. The run verb reaches the
same compute the room's buttons reach, and an API that got there without
charging would be a paywall with a second door. The page and the API now call
one function, and that class is what keeps them calling it.
"""

from __future__ import annotations

import json
from unittest import mock

from django.test import override_settings

from toto.ambrosia import filetree, registry
from toto.ambrosia.models import WorkspaceKind
from toto.ambrosia.tests.base import AmbrosiaTestCase
from toto.anastasia.tokens import CapsuleToken
from toto.dracena.tests.fakes import POOL


class WorkspaceApiTestCase(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace(name="Notebook", kind=WorkspaceKind.PYTHON)
        _row, self.raw = CapsuleToken.issue(owner=self.owner, label="laptop")

    def call(self, method, path, *, body=None, raw=None):
        kwargs = {"HTTP_AUTHORIZATION": f"Bearer {raw or self.raw}"}
        if body is not None:
            kwargs["content_type"] = "application/json"
            return getattr(self.client, method)(path, json.dumps(body), **kwargs)
        return getattr(self.client, method)(path, **kwargs)

    def api(self, suffix="", slug=None):
        return f"/api/v1/workspaces/{slug or self.ws.slug}{suffix}"


class AuthTests(WorkspaceApiTestCase):
    def test_every_endpoint_needs_a_token(self):
        """Asserted across the whole surface, not on one route. A single
        unguarded endpoint is the whole point of a gate lost."""
        reads = [self.api(), self.api("/tree"), "/api/v1/workspaces"]
        for path in reads:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
        writes = [self.api("/run"), self.api("/files/new"),
                  self.api("/folders/new")]
        for path in writes:
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path).status_code, 401)

    def test_a_bearer_call_needs_no_session_and_no_csrf(self):
        """The client is a desktop app: it holds neither. `csrf_exempt` is safe
        here precisely because a bearer token is not ambient credentials."""
        self.client.logout()
        response = self.call("get", self.api())
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_revoked_token_stops_working(self):
        row, raw = CapsuleToken.issue(owner=self.owner, label="doomed")
        row.revoke()
        self.assertEqual(self.call("get", self.api(), raw=raw).status_code, 401)

    def test_the_refusal_shape_is_the_capsule_apis(self):
        """One shape for the whole `/api/v1/` surface. A client already
        switches on `code`; a second shape for files would be a cost paid by
        every caller for nothing."""
        body = self.client.get(self.api()).json()
        self.assertIn("error", body)
        self.assertIn("code", body)
        self.assertNotIn("ok", body)


class OwnershipTests(WorkspaceApiTestCase):
    def test_somebody_elses_workspace_is_a_404_not_a_403(self):
        """A STRANGER's view. `self.owner` is staff — see the fixture — and
        staff legitimately read everything, so testing this with the owner's
        token would prove the opposite of what it says.
        """
        _row, raw = CapsuleToken.issue(owner=self.other, label="stranger")
        # The owner reaches their own workspace…
        self.assertEqual(self.call("get", self.api()).status_code, 200)
        # …and a stranger's token does not, without confirming it exists.
        response = self.call("get", self.api(), raw=raw)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(self.ws.name, response.content.decode())

    def test_a_staff_token_reads_every_workspace_and_that_is_the_rule(self):
        """SAID OUT LOUD because it is a real consequence, not an oversight.

        `permissions.can_view` admits any staff user to any workspace, and the
        API deliberately uses the same predicate the pages use — "a token
        reaches exactly what its owner reaches". So a staff member's token
        reads every room on the host, exactly as their browser does.

        If that is ever judged too wide over an API, the fix belongs in
        `can_view` where both doors would narrow together, NOT in a special
        case here that would let the two drift.
        """
        theirs = self.make_workspace(owner=self.other, name="Theirs",
                                     kind=WorkspaceKind.PYTHON)
        _row, raw = CapsuleToken.issue(owner=self.admin, label="staff")
        self.assertTrue(self.admin.is_staff)
        self.assertEqual(
            self.call("get", self.api(slug=theirs.slug), raw=raw).status_code,
            200)

    def test_the_list_holds_only_your_own(self):
        self.make_workspace(owner=self.other, name="Theirs",
                            kind=WorkspaceKind.PYTHON)
        rows = self.call("get", "/api/v1/workspaces").json()["workspaces"]
        self.assertEqual([r["slug"] for r in rows], [self.ws.slug])

    def test_staff_may_read_but_not_write_somebody_elses(self):
        """`can_view` admits staff; `can_edit` does not. The API uses the same
        two predicates the pages do, so a token grants nothing extra."""
        theirs = self.make_workspace(owner=self.other, name="Theirs",
                                     kind=WorkspaceKind.PYTHON)
        _row, raw = CapsuleToken.issue(owner=self.admin, label="staff")
        self.assertEqual(
            self.call("get", self.api(slug=theirs.slug), raw=raw).status_code, 200)
        self.assertEqual(
            self.call("post", self.api("/files/new", slug=theirs.slug),
                      body={"name": "x.py"}, raw=raw).status_code, 404)

    def test_a_staff_read_says_it_may_not_write(self):
        """The server answers `writable`, so a client never has to know the
        collaborator rules — and cannot be wrong the day they change."""
        theirs = self.make_workspace(owner=self.other, name="Theirs",
                                     kind=WorkspaceKind.PYTHON)
        _row, raw = CapsuleToken.issue(owner=self.admin, label="staff")
        body = self.call("get", self.api(slug=theirs.slug), raw=raw).json()
        self.assertFalse(body["writable"])
        self.assertTrue(self.call("get", self.api()).json()["writable"])


class ContractTests(WorkspaceApiTestCase):
    """The response keys, frozen against a literal.

    Adding a key passes; renaming or removing one fails. That is the only
    thing between a refactor in here and a broken client in another repo.
    """

    def test_the_workspace_shape(self):
        body = self.call("get", self.api()).json()
        self.assertLessEqual(
            {"slug", "name", "kind", "namespace", "owner", "writable",
             "created_at", "can_execute"},
            set(body))
        self.assertEqual(body["kind"], "python")
        self.assertEqual(body["namespace"], "dracena")

    def test_the_tree_shape_is_filetrees_own(self):
        """Not a second serialisation. If these drift, the room and the client
        are rendering different trees from the same bucket."""
        body = self.call("get", self.api("/tree")).json()
        self.assertEqual(body["items"], filetree.flatten(self.ws))

    def test_the_file_shape(self):
        pk = self._a_file()
        body = self.call("get", self.api(f"/files/{pk}")).json()
        self.assertLessEqual(
            {"pk", "name", "file_type", "content", "truncated", "readonly",
             "size"},
            set(body))

    def _a_file(self, name="notes.py"):
        # NOT main.py: `services.create_workspace` seeds one into an empty
        # folder, so asking for it again is a legitimate 409.
        created = self.call("post", self.api("/files/new"), body={"name": name})
        self.assertEqual(created.status_code, 201, created.content)
        return created.json()["pk"]


class FileTests(WorkspaceApiTestCase):
    def _a_file(self, name="notes.py"):
        # NOT main.py — a new workspace is seeded with one.
        created = self.call("post", self.api("/files/new"), body={"name": name})
        self.assertEqual(created.status_code, 201, created.content)
        return created.json()["pk"]

    def test_a_file_round_trips(self):
        """The whole point of the API: open it, change it, save it, read it
        back and get the same bytes."""
        pk = self._a_file()
        saved = self.call("post", self.api(f"/files/{pk}/save"),
                          body={"content": "print('hi')\n"})
        self.assertEqual(saved.status_code, 200, saved.content)
        body = self.call("get", self.api(f"/files/{pk}")).json()
        self.assertEqual(body["content"], "print('hi')\n")

    def test_unicode_survives_the_round_trip(self):
        pk = self._a_file()
        text = "# zażółć gęślą jaźń\nx = '→'\n"
        self.call("post", self.api(f"/files/{pk}/save"), body={"content": text})
        self.assertEqual(
            self.call("get", self.api(f"/files/{pk}")).json()["content"], text)

    def test_creating_returns_the_new_tree(self):
        """So a client does not have to make a second call to redraw."""
        body = self.call("post", self.api("/files/new"),
                         body={"name": "a.py"}).json()
        self.assertIn("items", body)
        self.assertTrue(any(i.get("name") == "a.py" for i in body["items"]))

    def test_a_folder_can_be_created(self):
        response = self.call("post", self.api("/folders/new"),
                             body={"name": "src"})
        self.assertEqual(response.status_code, 201, response.content)

    def test_renaming_and_deleting(self):
        pk = self._a_file()
        renamed = self.call("post", self.api(f"/files/{pk}/rename"),
                            body={"name": "other.py"})
        self.assertEqual(renamed.status_code, 200, renamed.content)
        self.assertTrue(any(i.get("name") == "other.py"
                            for i in renamed.json()["items"]))
        deleted = self.call("post", self.api(f"/files/{pk}/delete"))
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(any(i.get("name") == "other.py"
                             for i in deleted.json()["items"]))

    def test_a_file_outside_the_bucket_is_a_404(self):
        """The bucket is the boundary — `_workspace_file`'s rule, reused."""
        theirs = self.make_workspace(owner=self.other, name="Theirs",
                                     kind=WorkspaceKind.PYTHON)
        _row, raw = CapsuleToken.issue(owner=self.other, label="theirs")
        far = self.call("post", self.api("/files/new", slug=theirs.slug),
                        body={"name": "far.py"}, raw=raw).json()["pk"]
        self.assertEqual(
            self.call("get", self.api(f"/files/{far}")).status_code, 404)

    def test_a_refused_name_is_a_409_with_the_sentence(self):
        response = self.call("post", self.api("/files/new"), body={"name": ""})
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["error"])

    def test_writes_refuse_a_GET(self):
        pk = self._a_file()
        for suffix in (f"/files/{pk}/save", f"/files/{pk}/delete", "/files/new",
                       "/folders/new", "/run"):
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    self.call("get", self.api(suffix)).status_code, 405)

    def test_a_generated_file_is_read_only(self):
        """Enforced here and not only greyed in the tree: a hand-made call must
        not overwrite a compile's output either."""
        pk = self._a_file("out.pdf")
        with mock.patch.object(filetree, "is_artifact", return_value=True):
            response = self.call("post", self.api(f"/files/{pk}/save"),
                                 body={"content": "x"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "generated_file")


@override_settings(ANASTASIA_POOL=POOL)
class RunTests(WorkspaceApiTestCase):
    """One verb, dispatched through the registry."""

    def test_it_calls_the_language_apps_hook(self):
        self.assertIsNotNone(registry.for_kind(WorkspaceKind.PYTHON).run,
                             "dracena registered no run hook")
        fake = mock.Mock(return_value={"status": "ok", "stdout": "hi\n"})
        self._with_run(fake)
        response = self.call("post", self.api("/run"), body={"code": "1+1"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["stdout"], "hi\n")
        self.assertEqual(fake.call_args.kwargs["user"], self.owner)
        self.assertEqual(fake.call_args.kwargs["payload"], {"code": "1+1"})

    def test_a_refusal_carries_its_code_and_status(self):
        self._with_run(mock.Mock(side_effect=registry.RunRefused(
            "you hold no Capsule", code="no_capsule", status=409)))
        response = self.call("post", self.api("/run"), body={"code": "1"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "no_capsule")
        self.assertEqual(response.json()["error"], "you hold no Capsule")

    def test_a_queued_answer_says_so(self):
        """LaTeX answers with a receipt to poll; Python answers with output.
        `queued` is how a client tells them apart without knowing the kind."""
        self._with_run(mock.Mock(return_value={"status": "queued", "run": {}}))
        self.assertTrue(
            self.call("post", self.api("/run"),
                      body={}).json()["queued"])

    def test_a_synchronous_answer_is_not_queued(self):
        self._with_run(mock.Mock(return_value={"status": "ok", "stdout": ""}))
        self.assertFalse(
            self.call("post", self.api("/run"), body={}).json()["queued"])

    def test_a_reader_may_not_run(self):
        """Running is a write: it spends the owner's quota and charges them."""
        theirs = self.make_workspace(owner=self.other, name="Theirs",
                                     kind=WorkspaceKind.PYTHON)
        _row, raw = CapsuleToken.issue(owner=self.admin, label="staff")
        self.assertEqual(
            self.call("post", self.api("/run", slug=theirs.slug),
                      body={"code": "1"}, raw=raw).status_code, 404)

    def test_a_lab_with_no_run_verb_is_a_404(self):
        self._with_run(None)
        response = self.call("post", self.api("/run"), body={})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "no_run_verb")

    def _with_run(self, hook):
        """Swap the registered `run` for this test. WorkspaceApp is a frozen
        dataclass, so the entry is replaced rather than the attribute set —
        the same move `PartialSnapshotTests` makes in test_hibernation."""
        app = registry.for_kind(WorkspaceKind.PYTHON)
        self.addCleanup(registry._BY_KIND.__setitem__,
                        WorkspaceKind.PYTHON, app)
        registry._BY_KIND[WorkspaceKind.PYTHON] = registry.WorkspaceApp(
            namespace=app.namespace, kind=app.kind,
            extra_context=app.extra_context, extra_urls=app.extra_urls,
            teardown=app.teardown, main_id_for=app.main_id_for,
            settings_fields=app.settings_fields,
            settings_template=app.settings_template,
            room_panels=app.room_panels, snapshot=app.snapshot,
            restore=app.restore, run=hook)


class MeteringParityTests(WorkspaceApiTestCase):
    """THE PAYWALL TEST. The API must not reach compute on a cheaper path.

    Both labs extract their gate-quota-price-charge sequence into ONE
    request-free function, and both the page view and the registry hook call
    it. This asserts the identity rather than re-testing the sequence: if
    somebody later writes a second implementation for the API, these go red
    even though every individual behaviour test still passes.
    """

    def test_dracena_runs_through_the_same_function_as_its_page(self):
        from toto.dracena import views as dracena_views

        app = registry.for_kind(WorkspaceKind.PYTHON)
        with mock.patch.object(dracena_views, "run_workspace",
                               return_value={"status": "ok"}) as shared:
            app.run(self.ws, user=self.owner, payload={"code": "1"})
        self.assertTrue(shared.called,
                        "the run hook does not go through views.run_workspace, "
                        "so the API can reach compute without charging")

    def test_texlab_queues_through_the_same_function_as_its_page(self):
        from toto.texlab import views as texlab_views

        latex_ws = self.make_workspace(name="Paper", kind=WorkspaceKind.LATEX)
        app = registry.for_kind(WorkspaceKind.LATEX)
        with mock.patch.object(texlab_views, "queue_compile",
                               return_value={"status": "queued"}) as shared:
            app.run(latex_ws, user=self.owner, payload={})
        self.assertTrue(shared.called,
                        "the compile hook does not go through "
                        "views.queue_compile, so the API can queue without "
                        "charging")

    def test_every_installed_lab_offers_the_verb(self):
        """A lab that forgot to register `run` gives the client a 404 on the
        one endpoint it needs most, and nothing else would notice."""
        for kind, app in registry._BY_KIND.items():
            with self.subTest(kind=kind):
                self.assertIsNotNone(
                    app.run, f"{app.namespace} registered no run hook")


class PollTests(WorkspaceApiTestCase):
    """The other half of `run`, for a lab that only queues one.

    Without it a queued answer is a dead end: the client is handed a run id and
    has no session-free way to ask what became of it. A desktop LaTeX editor
    could start a compile and never learn whether it worked.
    """

    def setUp(self):
        super().setUp()
        self.latex = self.make_workspace(name="Paper", kind=WorkspaceKind.LATEX)

    def _url(self, run_id, slug=None):
        return f"/api/v1/workspaces/{slug or self.latex.slug}/runs/{run_id}"

    def test_a_python_workspace_has_nothing_to_poll(self):
        """Its Run IS the result. A 404 naming that is the honest answer — an
        endpoint that existed would only ever 404 anyway."""
        response = self.call("get", f"/api/v1/workspaces/{self.ws.slug}/runs/1")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "no_poll_verb")

    def test_it_calls_the_labs_hook(self):
        fake = mock.Mock(return_value={"id": 7, "state": "success",
                                       "finished": True})
        self._with_poll(fake)
        body = self.call("get", self._url(7)).json()
        self.assertEqual(body["state"], "success")
        self.assertEqual(fake.call_args.kwargs["run_id"], 7)
        self.assertEqual(fake.call_args.kwargs["user"], self.owner)

    def test_a_run_from_another_workspace_is_refused_by_the_lab(self):
        self._with_poll(mock.Mock(side_effect=registry.RunRefused(
            "no such compile in this workspace", code="no_such_run",
            status=404)))
        response = self.call("get", self._url(999))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "no_such_run")

    def test_polling_is_reading_so_a_viewer_may_watch_a_build(self):
        """`for_edit=False`. A staff member looking at a failed build should not
        have to be given write access to read the log."""
        self._with_poll(mock.Mock(return_value={"id": 1, "state": "failed"}))
        theirs = self.make_workspace(owner=self.other, name="Theirs",
                                     kind=WorkspaceKind.LATEX)
        _row, raw = CapsuleToken.issue(owner=self.admin, label="staff")
        response = self.call("get", self._url(1, slug=theirs.slug), raw=raw)
        self.assertEqual(response.status_code, 200)
        # …and that same reader may NOT queue one.
        self.assertEqual(
            self.call("post", f"/api/v1/workspaces/{theirs.slug}/run",
                      body={}, raw=raw).status_code, 404)

    def test_it_needs_a_token(self):
        self.assertEqual(self.client.get(self._url(1)).status_code, 401)

    def test_a_strangers_token_cannot_poll_your_build(self):
        """A NON-STAFF stranger, because `self.owner` is staff and staff read
        everything — testing this with the owner's token would prove the
        opposite of what it says. Same trap as OwnershipTests."""
        self._with_poll(mock.Mock(return_value={"id": 1, "state": "failed"}))
        _row, raw = CapsuleToken.issue(owner=self.other, label="stranger")
        response = self.call("get", self._url(1), raw=raw)
        self.assertEqual(response.status_code, 404)
        # And nothing about the build leaked in the refusal.
        self.assertNotIn("failed", response.content.decode())

    def _with_poll(self, hook):
        app = registry.for_kind(WorkspaceKind.LATEX)
        self.addCleanup(registry._BY_KIND.__setitem__, WorkspaceKind.LATEX, app)
        registry._BY_KIND[WorkspaceKind.LATEX] = registry.WorkspaceApp(
            namespace=app.namespace, kind=app.kind,
            extra_context=app.extra_context, extra_urls=app.extra_urls,
            teardown=app.teardown, main_id_for=app.main_id_for,
            settings_fields=app.settings_fields,
            settings_template=app.settings_template,
            room_panels=app.room_panels, snapshot=app.snapshot,
            restore=app.restore, run=app.run, poll=hook)


class RegistryContractTests(WorkspaceApiTestCase):
    """The two hooks, and what each lab must answer for.

    The API dispatches `run` and `poll` through the registry so it never
    imports a language app. That makes the registry the contract, and a lab
    that registers half of it produces an endpoint that 404s for a reason
    nobody can see from either side.
    """

    def test_a_lab_that_queues_must_also_offer_polling(self):
        """THE PAIR IS THE CONTRACT. A `run` that answers `status: queued` hands
        the client a receipt; without `poll` there is no token-authenticated
        way to redeem it, and a desktop LaTeX editor could start compiles and
        never learn whether they worked. That is exactly what shipped until
        2026-09-10.

        Asserted per lab rather than globally, because the converse is fine:
        dracena answers synchronously and correctly registers no `poll`.
        """
        latex = registry.for_kind(WorkspaceKind.LATEX)
        self.assertIsNotNone(latex.run, "texlab registers no run hook")
        self.assertIsNotNone(
            latex.poll,
            "texlab queues its compiles and registers no poll hook, so the "
            "run id it returns cannot be redeemed over the API")

    def test_a_lab_that_answers_at_once_needs_no_polling(self):
        """And must not pretend to. A `poll` on dracena would be an endpoint
        that can only 404 — its Run IS the result."""
        python = registry.for_kind(WorkspaceKind.PYTHON)
        self.assertIsNotNone(python.run)
        self.assertIsNone(
            python.poll,
            "dracena answers synchronously; a poll hook would only ever 404")

    def test_both_hooks_take_the_registry_shape(self):
        """One signature, so the base can call either without knowing which lab
        it has. A hook with a different shape fails at call time, in a request,
        rather than here."""
        import inspect

        for kind in (WorkspaceKind.PYTHON, WorkspaceKind.LATEX):
            app = registry.for_kind(kind)
            with self.subTest(kind=kind, hook="run"):
                params = inspect.signature(app.run).parameters
                self.assertEqual(list(params), ["workspace", "user", "payload"])
            if app.poll is None:
                continue
            with self.subTest(kind=kind, hook="poll"):
                params = inspect.signature(app.poll).parameters
                self.assertEqual(list(params), ["workspace", "user", "run_id"])

    def test_a_refusal_from_either_hook_keeps_its_status(self):
        """`RunRefused` carries the status because the KINDS differ: 403 you
        may not, 409 not in this state, 503 not right now. Flattening them to
        one code would make a client retry a permission error."""
        for status in (403, 409, 503):
            with self.subTest(status=status):
                self._with_run(mock.Mock(side_effect=registry.RunRefused(
                    "no", code="x", status=status)))
                self.assertEqual(
                    self.call("post", self.api("/run"), body={}).status_code,
                    status)

    def _with_run(self, hook):
        app = registry.for_kind(WorkspaceKind.PYTHON)
        self.addCleanup(registry._BY_KIND.__setitem__,
                        WorkspaceKind.PYTHON, app)
        registry._BY_KIND[WorkspaceKind.PYTHON] = registry.WorkspaceApp(
            namespace=app.namespace, kind=app.kind,
            extra_context=app.extra_context, extra_urls=app.extra_urls,
            teardown=app.teardown, main_id_for=app.main_id_for,
            settings_fields=app.settings_fields,
            settings_template=app.settings_template,
            room_panels=app.room_panels, snapshot=app.snapshot,
            restore=app.restore, run=hook, poll=app.poll)


class VaultRulesTests(WorkspaceApiTestCase):
    """The API must not be a way round the vault's own rules.

    Every write here goes through `services`, which is where size limits, type
    rules, the antivirus door and the storage levy's byte count live. A view
    that touched `VaultFile` directly would bypass all four, and the symptom
    would be a quota that stops adding up rather than an error.
    """

    def _a_file(self, name="notes.py"):
        return self.call("post", self.api("/files/new"),
                         body={"name": name}).json()["pk"]

    def test_a_write_updates_the_billed_byte_count(self):
        """`file_size_bytes` is what the storage levy bills. A write that did
        not update it would be free storage."""
        from toto.vault.models import VaultFile

        pk = self._a_file()
        self.call("post", self.api(f"/files/{pk}/save"),
                  body={"content": "x" * 500})
        self.assertEqual(VaultFile.objects.get(pk=pk).file_size_bytes, 500)

    def test_the_reported_size_is_the_stored_size(self):
        pk = self._a_file()
        body = self.call("post", self.api(f"/files/{pk}/save"),
                         body={"content": "abc"}).json()
        read = self.call("get", self.api(f"/files/{pk}")).json()
        self.assertEqual(body["size"], read["size"])

    def test_a_file_lands_in_the_workspace_bucket(self):
        """Not at the vault root, and not in somebody else's bucket. The bucket
        IS the boundary every file lookup here relies on."""
        from toto.vault.models import VaultFile

        pk = self._a_file()
        self.assertEqual(VaultFile.objects.get(pk=pk).bucket_id,
                         self.ws.bucket_id)

    def test_a_duplicate_name_is_refused_with_a_sentence(self):
        """`VaultFile.save()` raises on a duplicate key — unique per BUCKET —
        so this must be caught and phrased rather than 500ing."""
        self._a_file("twice.py")
        response = self.call("post", self.api("/files/new"),
                             body={"name": "twice.py"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("twice.py", response.json()["error"])

    def test_creating_in_a_directory_puts_it_there(self):
        made = self.call("post", self.api("/folders/new"),
                         body={"name": "src"}).json()
        folder = next(i for i in made["items"] if i["name"] == "src")
        created = self.call("post", self.api("/files/new"),
                            body={"name": "inner.py", "directory": folder["id"]})
        self.assertEqual(created.status_code, 201, created.content)
        row = next(i for i in created.json()["items"] if i["name"] == "inner.py")
        self.assertEqual(row["pid"], folder["id"])

    def test_a_directory_from_another_bucket_is_a_404(self):
        """`_resolve_dir`'s rule, reused: anywhere in this bucket, nowhere
        else. Without it a caller could plant a file in somebody else's tree."""
        theirs = self.make_workspace(owner=self.other, name="Theirs",
                                     kind=WorkspaceKind.PYTHON)
        response = self.call("post", self.api("/files/new"),
                             body={"name": "x.py",
                                   "directory": theirs.root_directory_id})
        self.assertEqual(response.status_code, 404)


class TreeShapeTests(WorkspaceApiTestCase):
    """The tree the client draws.

    Its exact shape matters more than most: the desktop client builds paths by
    walking `pid`, and a renamed key would leave every row at the top level
    with no error anywhere.
    """

    def test_a_row_carries_what_the_client_walks(self):
        self.call("post", self.api("/files/new"), body={"name": "a.py"})
        items = self.call("get", self.api("/tree")).json()["items"]
        row = next(i for i in items if i["name"] == "a.py")
        for key in ("t", "id", "pid", "depth", "name"):
            self.assertIn(key, row, f"the client walks {key}")

    def test_directories_and_files_are_told_apart_by_t(self):
        self.call("post", self.api("/folders/new"), body={"name": "src"})
        items = self.call("get", self.api("/tree")).json()["items"]
        kinds = {i["name"]: i["t"] for i in items}
        self.assertEqual(kinds["src"], "dir")

    def test_AN_ID_IS_ONLY_UNIQUE_WITHIN_ITS_KIND(self):
        """THE TRAP IN THIS SHAPE, and it is easy to walk into.

        `id` is the primary key of `VaultFile` OR of `VaultDirectory` — two
        tables, two sequences — so a directory and a file routinely share a
        number. Any `{row.id: row}` map silently loses one of them, and any
        "find the row with this id" matches the wrong kind.

        Written as a test rather than a comment because the first version of
        the depth test below did exactly that and produced a confusing
        off-by-one instead of an obvious failure. `pid` is a DIRECTORY id
        always, which is what makes the walk work at all.
        """
        made = self.call("post", self.api("/folders/new"),
                         body={"name": "outer"}).json()
        outer = next(i for i in made["items"] if i["name"] == "outer")
        self.call("post", self.api("/files/new"),
                  body={"name": "deep.py", "directory": outer["id"]})
        items = self.call("get", self.api("/tree")).json()["items"]

        ids = [i["id"] for i in items]
        self.assertNotEqual(
            len(ids), len(set(ids)),
            "ids happen to be unique in this fixture, so this test is no "
            "longer demonstrating the trap — pick a fixture that collides")
        # …and unique once the kind is part of the key, which is the fix.
        keyed = [(i["t"], i["id"]) for i in items]
        self.assertEqual(len(keyed), len(set(keyed)))

    def test_depth_and_pid_agree(self):
        """A child's depth is its parent's plus one. The client indents on
        `depth` and nests on `pid`; if they disagreed the tree would render
        with rows at the wrong level under the right parent.

        Keyed on `(t, id)` — see the test above for why `{id: row}` is wrong.
        """
        made = self.call("post", self.api("/folders/new"),
                         body={"name": "outer"}).json()
        outer = next(i for i in made["items"] if i["name"] == "outer")
        self.call("post", self.api("/files/new"),
                  body={"name": "deep.py", "directory": outer["id"]})
        items = self.call("get", self.api("/tree")).json()["items"]
        dirs = {i["id"]: i for i in items if i["t"] == "dir"}
        deep = next(i for i in items if i["name"] == "deep.py")
        self.assertEqual(deep["depth"], dirs[deep["pid"]]["depth"] + 1)

    def test_a_python_file_is_marked_runnable(self):
        self.call("post", self.api("/files/new"), body={"name": "script.py"})
        items = self.call("get", self.api("/tree")).json()["items"]
        row = next(i for i in items if i["name"] == "script.py")
        self.assertTrue(row["runnable"])

    def test_the_tree_is_scoped_to_this_workspace(self):
        """Two workspaces in two buckets must not see each other's files."""
        other_ws = self.make_workspace(name="Second", kind=WorkspaceKind.PYTHON)
        self.call("post", self.api("/files/new", slug=other_ws.slug),
                  body={"name": "elsewhere.py"})
        names = {i["name"] for i in
                 self.call("get", self.api("/tree")).json()["items"]}
        self.assertNotIn("elsewhere.py", names)
