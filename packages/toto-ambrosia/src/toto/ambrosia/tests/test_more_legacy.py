"""The pre-split `/ambrosia/...` addresses on a host with no language lab.

This host mounts `legacy_urls` and registers no lab (dracena and texlab are
parked), so every old bookmark has nowhere to go. What it must do then is
answer a plain 404 — never a 500 from reversing a namespace nobody mounts, and
never a redirect loop. The registry is emptied for each test rather than
trusted to be empty: another suite in the same process may have registered the
test lab.
"""

from unittest import mock

from toto.ambrosia import registry
from toto.ambrosia.models import Workspace

from .base import AmbrosiaTestCase


class NoLabLegacyTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        for table in (registry._BY_NAMESPACE, registry._BY_KIND):
            patcher = mock.patch.dict(table, clear=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client.force_login(self.owner)

    def test_the_old_lobby_is_a_404_when_no_lab_is_installed(self):
        for path in ("/ambrosia/", "/ambrosia/lobby/old/bookmark"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_an_old_room_link_is_a_404_when_its_lab_is_gone(self):
        workspace = self.make_workspace(name="Analysis")
        for path in (f"/ambrosia/w/{workspace.slug}/",
                     f"/ambrosia/w/{workspace.slug}",
                     f"/ambrosia/w/{workspace.slug}/files/1/?line=3"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        self.assertTrue(Workspace.objects.filter(pk=workspace.pk).exists())

    def test_an_unknown_workspace_is_a_404(self):
        self.assertEqual(self.client.get("/ambrosia/w/no-such-room/").status_code, 404)

    def test_a_repo_can_still_tell_a_workspace_folder_from_a_plain_one(self):
        # toto.repo asks this on REPO_WORKSPACES_ONLY hosts, with or without a lab.
        from toto.repo import services as repo_services

        workspace = self.make_workspace(name="Analysis")
        self.assertTrue(repo_services.is_workspace_root(workspace.root_directory))
        from toto.vault.models import VaultDirectory

        plain = VaultDirectory.objects.create(bucket=workspace.bucket, owner=self.owner,
                                              name="plain")
        self.assertFalse(repo_services.is_workspace_root(plain))
