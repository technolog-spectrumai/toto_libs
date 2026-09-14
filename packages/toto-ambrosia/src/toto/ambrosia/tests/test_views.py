"""Pages, the JSON endpoints, and the gate on running code.

Every page here is served under a language app's namespace, and no language
app is installed where this suite runs, so they are reached through the test
lab (tests/testlab.py). The Run endpoint and the gate tests written against it
were dracena's (`dracena:execute`) and left with it on 2026-09-14; the
permission that gate asked is ambrosia's and is tested at the bottom.
"""

import json
from unittest import mock

from toto.vault.models import Bucket, VaultDirectory, VaultFile

from toto.ambrosia import services
from toto.ambrosia.models import Workspace

from . import testlab
from .base import AmbrosiaTestCase, TestlabTestCase


class LobbyTests(TestlabTestCase):
    def test_the_lobby_renders(self):
        self.client.force_login(self.owner)
        response = self.client.get(testlab.url("lobby"))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_users_are_sent_to_login(self):
        self.assertEqual(self.client.get(testlab.url("lobby")).status_code,
                         302)

    def test_only_your_own_workspaces_are_listed(self):
        mine = self.make_workspace(name="Mine")
        theirs = self.make_workspace(owner=self.other, name="Theirs")
        self.client.force_login(self.owner)
        body = self.client.get(testlab.url("lobby")).content.decode()
        self.assertIn("Mine", body)
        self.assertNotIn("Theirs", body)

    def test_creating_a_workspace_from_the_lobby_redirects_into_it(self):
        bucket = self.make_bucket()
        self.client.force_login(self.owner)
        response = self.client.post(
            testlab.url("workspace_create"),
            {"name": "Fresh", "kind": "python", "bucket": bucket.pk,
             "directory": "", "new_directory_name": "fresh"})
        self.assertEqual(response.status_code, 302)
        ws = Workspace.objects.get(name="Fresh")
        self.assertIn(ws.slug, response["Location"])
        self.assertEqual(ws.bucket_id, bucket.pk)
        self.assertEqual(ws.root_directory.name, "fresh")

    def test_creating_without_a_bucket_goes_back_to_the_lobby(self):
        self.client.force_login(self.owner)
        response = self.client.post(testlab.url("workspace_create"),
                                    {"name": "Fresh", "kind": "python"})
        self.assertRedirects(response, testlab.url("lobby"))
        self.assertFalse(Workspace.objects.filter(name="Fresh").exists())

    def test_you_cannot_post_a_bucket_belonging_to_someone_else(self):
        theirs = Bucket.objects.create(name="Not yours", owner=self.other,
                                       slug="not-yours", storage_backend="local")
        self.client.force_login(self.owner)
        response = self.client.post(
            testlab.url("workspace_create"),
            {"name": "Sneaky", "kind": "python", "bucket": theirs.pk,
             "new_directory_name": "sneaky"})
        self.assertRedirects(response, testlab.url("lobby"))
        self.assertFalse(Workspace.objects.filter(name="Sneaky").exists())

    def test_the_lobby_says_so_when_you_have_no_buckets(self):
        self.client.force_login(self.owner)
        response = self.client.get(testlab.url("lobby"))
        self.assertFalse(response.context["has_buckets"])
        self.assertIn("no buckets yet", response.content.decode())


class WorkspaceRoomTests(TestlabTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace()
        self.main = VaultFile.objects.get(directory=self.ws.root_directory)

    def _url(self, name="workspace", **kw):
        return testlab.url(name, slug=self.ws.slug, **kw)

    def test_the_owner_can_open_the_room(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self._url()).status_code, 200)

    def test_a_stranger_gets_404_not_403(self):
        # 403 would confirm the workspace exists.
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_staff_may_look_at_someone_elses_workspace(self):
        theirs = self.make_workspace(owner=self.other, name="Theirs")
        self.client.force_login(self.admin)
        response = self.client.get(testlab.url("workspace", slug=theirs.slug))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["readonly"],
                        "staff look, they do not edit")

    def test_the_room_ships_the_tree_but_no_file_content(self):
        # A hundred-file workspace must not inline a hundred files.
        services.write_file(vault_file=self.main,
                            content="SECRET_MARKER_IN_FILE = 1\n")
        self.client.force_login(self.owner)
        body = self.client.get(self._url()).content.decode()
        self.assertIn("main.py", body)
        self.assertNotIn("SECRET_MARKER_IN_FILE", body)

    def test_the_room_carries_its_config_island(self):
        self.client.force_login(self.owner)
        body = self.client.get(self._url()).content.decode()
        self.assertIn('id="ambrosia-config"', body)
        self.assertIn('id="ambrosia-tree"', body)

    def test_the_room_offers_local_git(self):
        # Git used to be refused outright on this host, on the theory that it
        # "rides the gitea sidecar". Only the REMOTE half ever did, and it is a
        # separate app now: a repository, its branches and its merges are local
        # files, and any host can have all of it.
        #
        # This test used to END by asserting toto.gitea was NOT installed, "a
        # federation child cannot run the gitea sidecar". That was true of
        # PLACIDIA, which owned the workspaces when this was written and was a
        # federation consumer; it is not a property of the room, and it is false
        # here — zenobia is the provider and runs the forge. Enabling BUILD_REPO
        # is what surfaced it. The complementary assertion below is the better
        # one anyway: it proves the two halves COMPOSE, which is the thing the
        # gitvault split was for.
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.repo"):
            self.skipTest("built without BUILD_REPO")

        self.client.force_login(self.owner)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        # The toolbar renders: the room's own context carries the repo ctx.
        self.assertIn("repo_ctx", response.context)
        body = response.content.decode()
        self.assertIn("GitUIPendingCtx", body)

        from toto.repo.remotes import registry

        if django_apps.is_installed("toto.gitea"):
            # Both halves installed: the forge registers a credential provider
            # that toto.repo autodiscovers, and pushing to it authenticates.
            self.assertIn("Gitea", registry.names())
        else:
            # Local git alone. A remote can still be set; nothing here holds
            # credentials for it, which is the honest answer, not an error.
            self.assertNotIn("Gitea", registry.names())


class CloseAndDestroyTests(TestlabTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace(name="Doomed")
        self.client.force_login(self.owner)

    def _url(self, name):
        return testlab.url(name, slug=self.ws.slug)

    def test_closing_keeps_the_folder_and_the_files(self):
        root_pk = self.ws.root_directory_id
        response = self.client.post(self._url("workspace_close"))
        self.assertRedirects(response, testlab.url("lobby"))
        self.assertFalse(Workspace.objects.filter(pk=self.ws.pk).exists())
        self.assertTrue(VaultDirectory.objects.filter(pk=root_pk).exists())
        self.assertTrue(VaultFile.objects.filter(directory_id=root_pk).exists())

    def test_destroying_needs_the_name_typed_back(self):
        response = self.client.post(self._url("workspace_destroy"),
                                    {"confirm": "not the name"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("destroy=1", response["Location"])
        self.assertTrue(Workspace.objects.filter(pk=self.ws.pk).exists())

    def test_destroying_with_the_name_takes_the_folder_with_it(self):
        root_pk = self.ws.root_directory_id
        response = self.client.post(self._url("workspace_destroy"),
                                    {"confirm": "Doomed"})
        self.assertRedirects(response, testlab.url("lobby"))
        self.assertFalse(Workspace.objects.filter(pk=self.ws.pk).exists())
        self.assertFalse(VaultDirectory.objects.filter(pk=root_pk).exists())
        self.assertFalse(VaultFile.objects.filter(directory_id=root_pk).exists())

    def test_the_bucket_itself_survives_a_destroy(self):
        bucket_pk = self.ws.bucket_id
        self.client.post(self._url("workspace_destroy"),
                         {"confirm": "Doomed"})
        self.assertTrue(Bucket.objects.filter(pk=bucket_pk).exists())

    def test_a_stranger_cannot_destroy_your_workspace(self):
        self.client.force_login(self.other)
        response = self.client.post(self._url("workspace_destroy"),
                                    {"confirm": "Doomed"})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Workspace.objects.filter(pk=self.ws.pk).exists())

    def test_staff_look_but_do_not_destroy(self):
        self.client.force_login(self.admin)
        response = self.client.post(self._url("workspace_destroy"),
                                    {"confirm": "Doomed"})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Workspace.objects.filter(pk=self.ws.pk).exists())

    def test_both_buttons_refuse_a_get(self):
        for name in ("workspace_close", "workspace_destroy"):
            self.assertEqual(self.client.get(self._url(name)).status_code, 405)


class FileEndpointTests(TestlabTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace()
        self.main = VaultFile.objects.get(directory=self.ws.root_directory)
        self.client.force_login(self.owner)

    def _url(self, name, **kw):
        return testlab.url(name, slug=self.ws.slug, **kw)

    def _build_folder(self):
        # A bare build/ folder: the base's read-only rule keys on the NAME.
        # Producing one is a compiling lab's business and not under test here.
        return VaultDirectory.objects.create(
            name="build", bucket=self.ws.bucket, owner=self.owner,
            parent=self.ws.root_directory)

    def test_content_comes_back_as_json(self):
        services.write_file(vault_file=self.main, content="answer = 42\n")
        data = self.client.get(
            self._url("file_content", pk=self.main.pk)).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["content"], "answer = 42\n")
        self.assertEqual(data["file_type"], "python")

    def test_saving_persists(self):
        response = self.client.post(
            self._url("file_save", pk=self.main.pk),
            data=json.dumps({"content": "saved = True\n"}),
            content_type="application/json")
        self.assertTrue(response.json()["ok"])
        self.main.refresh_from_db()
        self.assertEqual(services.read_file(self.main)[0], "saved = True\n")

    def test_a_file_from_another_bucket_is_not_reachable(self):
        theirs = self.make_workspace(name="Other",
                                     bucket=self.make_bucket(name="Theirs"))
        alien = VaultFile.objects.get(directory=theirs.root_directory)
        response = self.client.get(
            self._url("file_content", pk=alien.pk))
        self.assertEqual(response.status_code, 404)

    def test_a_generated_file_opens_read_only(self):
        log = services.create_file(workspace=self.ws, user=self.owner,
                                   filename="main.log",
                                   directory=self._build_folder())
        services.write_file(vault_file=log, content="! Undefined control sequence.\n")
        data = self.client.get(
            self._url("file_content", pk=log.pk)).json()
        self.assertTrue(data["readonly"])
        self.assertFalse(data["truncated"])
        self.assertIn("Undefined", data["content"])

    def test_saving_a_generated_file_is_refused(self):
        # Enforced on the server, not by the UI's good manners: a hand-made POST
        # must not overwrite a compile's output either, and the next run would
        # discard the edit regardless.
        log = services.create_file(workspace=self.ws, user=self.owner,
                                   filename="main.log",
                                   directory=self._build_folder())
        services.write_file(vault_file=log, content="the log\n")
        response = self.client.post(
            self._url("file_save", pk=log.pk),
            data=json.dumps({"content": "nonsense"}),
            content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertIn("read-only", response.json()["error"])
        self.assertEqual(services.read_file(log)[0], "the log\n")

    def test_a_huge_log_comes_back_trimmed_to_its_tail(self):
        # A pdfTeX log runs to megabytes when a package is chatty, and the tail
        # is where the errors are. Refusing to open it would be worse.
        log = services.create_file(workspace=self.ws, user=self.owner,
                                   filename="big.log",
                                   directory=self._build_folder())
        filler = "x" * 99 + "\n"
        body = filler * ((services.MAX_READ_BYTES // 100) + 50)
        services.write_file(vault_file=log, content=body + "! the last error\n")
        data = self.client.get(
            self._url("file_content", pk=log.pk)).json()
        self.assertTrue(data["truncated"])
        self.assertLess(len(data["content"]), len(body))
        self.assertTrue(data["content"].endswith("! the last error\n"))
        self.assertIn("trimmed", data["content"].split("\n")[0])

    def test_a_stranger_cannot_read_a_file(self):
        self.client.force_login(self.other)
        response = self.client.get(
            self._url("file_content", pk=self.main.pk))
        self.assertEqual(response.status_code, 404)

    def test_staff_may_read_but_not_save(self):
        theirs = self.make_workspace(owner=self.other, name="Theirs")
        their_file = VaultFile.objects.get(directory=theirs.root_directory)
        self.client.force_login(self.admin)
        read = self.client.get(testlab.url(
            "file_content", slug=theirs.slug, pk=their_file.pk))
        self.assertEqual(read.status_code, 200)
        write = self.client.post(
            testlab.url("file_save", slug=theirs.slug, pk=their_file.pk),
            data=json.dumps({"content": "nope"}), content_type="application/json")
        self.assertEqual(write.status_code, 404)

    def test_creating_a_file_returns_the_refreshed_tree(self):
        response = self.client.post(
            self._url("file_create"),
            data=json.dumps({"name": "extra.py"}),
            content_type="application/json")
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIn("extra.py", [i["name"] for i in data["items"]])

    def test_creating_a_folder_returns_the_refreshed_tree(self):
        response = self.client.post(
            self._url("dir_create"),
            data=json.dumps({"name": "pkg"}),
            content_type="application/json")
        self.assertIn("pkg", [i["name"] for i in response.json()["items"]])

    def test_a_duplicate_name_reports_the_reason(self):
        response = self.client.post(
            self._url("file_create"),
            data=json.dumps({"name": "main.py"}),
            content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertIn("already exists", response.json()["error"])

    def test_deleting_removes_the_file(self):
        response = self.client.post(
            self._url("file_delete", pk=self.main.pk),
            content_type="application/json")
        self.assertTrue(response.json()["ok"])
        self.assertFalse(VaultFile.objects.filter(pk=self.main.pk).exists())

    def test_endpoints_refuse_a_get_where_they_mutate(self):
        self.assertEqual(
            self.client.get(self._url("file_create")).status_code, 405)


class PermissionSettingTests(AmbrosiaTestCase):
    def test_the_access_setting_is_honoured(self):
        from toto.ambrosia import permissions

        # EXECUTION_ACCESS is read once, at import, so the module's copy is the
        # one that has to change. This used override_settings until
        # 2026-09-14 and asserted only that a superuser may run, which is true
        # under the "staff" default too, so it passed without proving anything.
        with mock.patch.object(permissions, "EXECUTION_ACCESS", "superuser"):
            self.assertTrue(permissions.can_execute(self.admin))
            self.assertFalse(permissions.can_execute(self.owner),
                             "staff is not enough once superuser is required")

    def test_staff_may_execute_by_default(self):
        from toto.ambrosia import permissions

        self.assertTrue(permissions.can_execute(self.owner))
        self.assertFalse(permissions.can_execute(self.other))

    def test_the_refusal_says_what_to_do_about_it(self):
        from toto.ambrosia import permissions

        message = permissions.execution_refusal()
        self.assertIn("privileges", message)
        self.assertIn("AMBROSIA_EXECUTION_ACCESS", message)
