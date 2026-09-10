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
