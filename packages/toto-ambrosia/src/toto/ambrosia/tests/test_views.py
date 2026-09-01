"""Pages, the JSON endpoints, and the gate on running code."""

import unittest

import json
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from toto.vault.models import Bucket, VaultDirectory, VaultFile

from toto.ambrosia import services
from toto.ambrosia.models import Workspace

from .base import AmbrosiaTestCase




class LobbyTests(AmbrosiaTestCase):
    def test_the_lobby_renders(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dracena:lobby"))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_users_are_sent_to_login(self):
        self.assertEqual(self.client.get(reverse("dracena:lobby")).status_code,
                         302)

    def test_only_your_own_workspaces_are_listed(self):
        mine = self.make_workspace(name="Mine")
        theirs = self.make_workspace(owner=self.other, name="Theirs")
        self.client.force_login(self.owner)
        body = self.client.get(reverse("dracena:lobby")).content.decode()
        self.assertIn("Mine", body)
        self.assertNotIn("Theirs", body)

    def test_creating_a_workspace_from_the_lobby_redirects_into_it(self):
        bucket = self.make_bucket()
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("dracena:workspace_create"),
            {"name": "Fresh", "kind": "python", "bucket": bucket.pk,
             "directory": "", "new_directory_name": "fresh"})
        self.assertEqual(response.status_code, 302)
        ws = Workspace.objects.get(name="Fresh")
        self.assertIn(ws.slug, response["Location"])
        self.assertEqual(ws.bucket_id, bucket.pk)
        self.assertEqual(ws.root_directory.name, "fresh")

    def test_creating_without_a_bucket_goes_back_to_the_lobby(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("dracena:workspace_create"),
                                    {"name": "Fresh", "kind": "python"})
        self.assertRedirects(response, reverse("dracena:lobby"))
        self.assertFalse(Workspace.objects.filter(name="Fresh").exists())

    def test_you_cannot_post_a_bucket_belonging_to_someone_else(self):
        theirs = Bucket.objects.create(name="Not yours", owner=self.other,
                                       slug="not-yours", storage_backend="local")
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("dracena:workspace_create"),
            {"name": "Sneaky", "kind": "python", "bucket": theirs.pk,
             "new_directory_name": "sneaky"})
        self.assertRedirects(response, reverse("dracena:lobby"))
        self.assertFalse(Workspace.objects.filter(name="Sneaky").exists())

    def test_the_lobby_says_so_when_you_have_no_buckets(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dracena:lobby"))
        self.assertFalse(response.context["has_buckets"])
        self.assertIn("no buckets yet", response.content.decode())


class WorkspaceRoomTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace()
        self.main = VaultFile.objects.get(directory=self.ws.root_directory)

    def _url(self, name="dracena:workspace", **kw):
        return reverse(name, kwargs={"slug": self.ws.slug, **kw})

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
        url = reverse("dracena:workspace", kwargs={"slug": theirs.slug})
        response = self.client.get(url)
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


class CloseAndDestroyTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace(name="Doomed")
        self.client.force_login(self.owner)

    def _url(self, name):
        return reverse(name, kwargs={"slug": self.ws.slug})

    def test_closing_keeps_the_folder_and_the_files(self):
        root_pk = self.ws.root_directory_id
        response = self.client.post(self._url("dracena:workspace_close"))
        self.assertRedirects(response, reverse("dracena:lobby"))
        self.assertFalse(Workspace.objects.filter(pk=self.ws.pk).exists())
        self.assertTrue(VaultDirectory.objects.filter(pk=root_pk).exists())
        self.assertTrue(VaultFile.objects.filter(directory_id=root_pk).exists())

    def test_destroying_needs_the_name_typed_back(self):
        response = self.client.post(self._url("dracena:workspace_destroy"),
                                    {"confirm": "not the name"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("destroy=1", response["Location"])
        self.assertTrue(Workspace.objects.filter(pk=self.ws.pk).exists())

    def test_destroying_with_the_name_takes_the_folder_with_it(self):
        root_pk = self.ws.root_directory_id
        response = self.client.post(self._url("dracena:workspace_destroy"),
                                    {"confirm": "Doomed"})
        self.assertRedirects(response, reverse("dracena:lobby"))
        self.assertFalse(Workspace.objects.filter(pk=self.ws.pk).exists())
        self.assertFalse(VaultDirectory.objects.filter(pk=root_pk).exists())
        self.assertFalse(VaultFile.objects.filter(directory_id=root_pk).exists())

    def test_the_bucket_itself_survives_a_destroy(self):
        bucket_pk = self.ws.bucket_id
        self.client.post(self._url("dracena:workspace_destroy"),
                         {"confirm": "Doomed"})
        self.assertTrue(Bucket.objects.filter(pk=bucket_pk).exists())

    def test_a_stranger_cannot_destroy_your_workspace(self):
        self.client.force_login(self.other)
        response = self.client.post(self._url("dracena:workspace_destroy"),
                                    {"confirm": "Doomed"})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Workspace.objects.filter(pk=self.ws.pk).exists())

    def test_staff_look_but_do_not_destroy(self):
        self.client.force_login(self.admin)
        response = self.client.post(self._url("dracena:workspace_destroy"),
                                    {"confirm": "Doomed"})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Workspace.objects.filter(pk=self.ws.pk).exists())

    def test_both_buttons_refuse_a_get(self):
        for name in ("dracena:workspace_close", "dracena:workspace_destroy"):
            self.assertEqual(self.client.get(self._url(name)).status_code, 405)


class FileEndpointTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace()
        self.main = VaultFile.objects.get(directory=self.ws.root_directory)
        self.client.force_login(self.owner)

    def _url(self, name, **kw):
        return reverse(name, kwargs={"slug": self.ws.slug, **kw})

    def test_content_comes_back_as_json(self):
        services.write_file(vault_file=self.main, content="answer = 42\n")
        data = self.client.get(
            self._url("dracena:file_content", pk=self.main.pk)).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["content"], "answer = 42\n")
        self.assertEqual(data["file_type"], "python")

    def test_saving_persists(self):
        response = self.client.post(
            self._url("dracena:file_save", pk=self.main.pk),
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
            self._url("dracena:file_content", pk=alien.pk))
        self.assertEqual(response.status_code, 404)

    def test_a_generated_file_opens_read_only(self):
        # a bare build/ folder — the base's read-only rule keys on the NAME;
        # producing one is texlab's business and not under test here
        from toto.vault.models import VaultDirectory
        build = VaultDirectory.objects.create(
            name="build", bucket=self.ws.bucket, owner=self.owner,
            parent=self.ws.root_directory)
        log = services.create_file(workspace=self.ws, user=self.owner,
                                   filename="main.log", directory=build)
        services.write_file(vault_file=log, content="! Undefined control sequence.\n")
        data = self.client.get(
            self._url("dracena:file_content", pk=log.pk)).json()
        self.assertTrue(data["readonly"])
        self.assertFalse(data["truncated"])
        self.assertIn("Undefined", data["content"])

    def test_saving_a_generated_file_is_refused(self):
        # Enforced on the server, not by the UI's good manners: a hand-made POST
        # must not overwrite a compile's output either, and the next run would
        # discard the edit regardless.
        # a bare build/ folder — the base's read-only rule keys on the NAME;
        # producing one is texlab's business and not under test here
        from toto.vault.models import VaultDirectory
        build = VaultDirectory.objects.create(
            name="build", bucket=self.ws.bucket, owner=self.owner,
            parent=self.ws.root_directory)
        log = services.create_file(workspace=self.ws, user=self.owner,
                                   filename="main.log", directory=build)
        services.write_file(vault_file=log, content="the log\n")
        response = self.client.post(
            self._url("dracena:file_save", pk=log.pk),
            data=json.dumps({"content": "nonsense"}),
            content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertIn("read-only", response.json()["error"])
        self.assertEqual(services.read_file(log)[0], "the log\n")

    def test_a_huge_log_comes_back_trimmed_to_its_tail(self):
        # A pdfTeX log runs to megabytes when a package is chatty, and the tail
        # is where the errors are. Refusing to open it would be worse.
        # a bare build/ folder — the base's read-only rule keys on the NAME;
        # producing one is texlab's business and not under test here
        from toto.vault.models import VaultDirectory
        build = VaultDirectory.objects.create(
            name="build", bucket=self.ws.bucket, owner=self.owner,
            parent=self.ws.root_directory)
        log = services.create_file(workspace=self.ws, user=self.owner,
                                   filename="big.log", directory=build)
        filler = "x" * 99 + "\n"
        body = filler * ((services.MAX_READ_BYTES // 100) + 50)
        services.write_file(vault_file=log, content=body + "! the last error\n")
        data = self.client.get(
            self._url("dracena:file_content", pk=log.pk)).json()
        self.assertTrue(data["truncated"])
        self.assertLess(len(data["content"]), len(body))
        self.assertTrue(data["content"].endswith("! the last error\n"))
        self.assertIn("trimmed", data["content"].split("\n")[0])

    def test_a_stranger_cannot_read_a_file(self):
        self.client.force_login(self.other)
        response = self.client.get(
            self._url("dracena:file_content", pk=self.main.pk))
        self.assertEqual(response.status_code, 404)

    def test_staff_may_read_but_not_save(self):
        theirs = self.make_workspace(owner=self.other, name="Theirs")
        their_file = VaultFile.objects.get(directory=theirs.root_directory)
        self.client.force_login(self.admin)
        read = self.client.get(reverse("dracena:file_content",
                                       kwargs={"slug": theirs.slug,
                                               "pk": their_file.pk}))
        self.assertEqual(read.status_code, 200)
        write = self.client.post(
            reverse("dracena:file_save",
                    kwargs={"slug": theirs.slug, "pk": their_file.pk}),
            data=json.dumps({"content": "nope"}), content_type="application/json")
        self.assertEqual(write.status_code, 404)

    def test_creating_a_file_returns_the_refreshed_tree(self):
        response = self.client.post(
            self._url("dracena:file_create"),
            data=json.dumps({"name": "extra.py"}),
            content_type="application/json")
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIn("extra.py", [i["name"] for i in data["items"]])

    def test_creating_a_folder_returns_the_refreshed_tree(self):
        response = self.client.post(
            self._url("dracena:dir_create"),
            data=json.dumps({"name": "pkg"}),
            content_type="application/json")
        self.assertIn("pkg", [i["name"] for i in response.json()["items"]])

    def test_a_duplicate_name_reports_the_reason(self):
        response = self.client.post(
            self._url("dracena:file_create"),
            data=json.dumps({"name": "main.py"}),
            content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertIn("already exists", response.json()["error"])

    def test_deleting_removes_the_file(self):
        response = self.client.post(
            self._url("dracena:file_delete", pk=self.main.pk),
            content_type="application/json")
        self.assertTrue(response.json()["ok"])
        self.assertFalse(VaultFile.objects.filter(pk=self.main.pk).exists())

    def test_endpoints_refuse_a_get_where_they_mutate(self):
        self.assertEqual(
            self.client.get(self._url("dracena:file_create")).status_code, 405)


@override_settings(
    ANASTASIA_POOL={"cpu_millicores": 4000, "ram_mb": 8192,
                    "scratch_mb": 8192, "pids": 2048},
    ANASTASIA_RUNTIME_BACKEND="toto.dracena.tests.fakes.FakeKernelBackend",
)
class ExecutionGateTests(AmbrosiaTestCase):
    """Running code is a privilege, not a consequence of owning a workspace.

    Two privileges now, and they refuse in a different order. Staff-ness is
    checked first (403); holding a Compute Gear is checked second (409). The
    owner here is given one, so the tests below reach the gate they are
    actually about instead of stopping at "you have nowhere to run this".
    """

    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace()
        from toto.anastasia import services
        from toto.anastasia.limits import Limits

        lease = services.reserve(owner=self.owner, name="gate",
                                 limits=Limits(2000, 2048, 1024, 512))
        services.mount(lease=lease, actor=self.owner)

    def _execute_as(self, user, code="1 + 1"):
        self.client.force_login(user)
        return self.client.post(
            reverse("dracena:execute", kwargs={"slug": self.ws.slug}),
            data=json.dumps({"code": code}), content_type="application/json")

    def test_a_stranger_cannot_even_reach_the_endpoint(self):
        response = self._execute_as(self.other)
        self.assertEqual(response.status_code, 404)

    def test_a_non_staff_owner_of_their_own_workspace_is_refused(self):
        theirs = self.make_workspace(owner=self.other, name="Theirs")
        self.client.force_login(self.other)
        response = self.client.post(
            reverse("dracena:execute", kwargs={"slug": theirs.slug}),
            data=json.dumps({"code": "1"}), content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertIn("server", response.json()["error"].lower())

    def test_empty_code_is_refused_before_the_kernel_is_touched(self):
        response = self._execute_as(self.owner, code="   ")
        self.assertEqual(response.status_code, 400)

    def test_a_build_without_a_kernel_says_so(self):
        from toto.dracena import kernel

        with mock.patch.object(
                kernel, "execute",
                side_effect=kernel.KernelUnavailable("no interpreter here")):
            response = self._execute_as(self.owner)
        self.assertEqual(response.status_code, 503)
        self.assertIn("no interpreter here", response.json()["error"])

    def test_a_successful_run_returns_the_console_shape(self):
        from toto.dracena import kernel

        fake = {"status": "ok", "stdout": "hi\n", "stderr": "", "rich": [],
                "execution_count": 1, "error": ""}
        with mock.patch.object(kernel, "execute", return_value=fake):
            data = self._execute_as(self.owner).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["stdout"], "hi\n")
        self.assertEqual(data["execution_count"], 1)

    def test_kernel_actions_are_gated_too(self):
        theirs = self.make_workspace(owner=self.other, name="Theirs")
        self.client.force_login(self.other)
        response = self.client.post(
            reverse("dracena:kernel_action",
                    kwargs={"slug": theirs.slug, "action": "start"}))
        self.assertEqual(response.status_code, 403)

    def test_an_unknown_kernel_action_is_refused(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("dracena:kernel_action",
                    kwargs={"slug": self.ws.slug, "action": "explode"}))
        self.assertEqual(response.status_code, 400)


class PermissionSettingTests(AmbrosiaTestCase):
    def test_the_access_setting_is_honoured(self):
        from toto.ambrosia import permissions

        with override_settings(AMBROSIA_EXECUTION_ACCESS="superuser"):
            # Read at import time, so reload the module's view of it.
            self.assertTrue(permissions.can_execute(self.admin))

    def test_staff_may_execute_by_default(self):
        from toto.ambrosia import permissions

        self.assertTrue(permissions.can_execute(self.owner))
        self.assertFalse(permissions.can_execute(self.other))

    def test_the_refusal_says_what_to_do_about_it(self):
        from toto.ambrosia import permissions

        message = permissions.execution_refusal()
        self.assertIn("privileges", message)
        self.assertIn("AMBROSIA_EXECUTION_ACCESS", message)
