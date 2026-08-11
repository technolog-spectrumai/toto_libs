"""The dashboard landing page: two choices, and where each repo links back to.

The list is READ-ONLY by contract — it says what exists and sends you to the
surface that owns it. Committing, merging, branching and switching all happen
in the workspace or the editor, where the files are.
"""

from django.apps import apps as django_apps
from django.test import override_settings
from django.urls import reverse

from toto.gitvault import pages

from .base import GitvaultTestCase


class SurfaceLinkTests(GitvaultTestCase):
    def test_a_workspace_repo_links_to_its_room(self):
        # Workspace attribution was deleted on the premise that gitvault could
        # never run where the workspaces live. It can now, so a repo rooted on
        # a workspace folder must name the workspace and link to its room —
        # otherwise the host that owns the labs is exactly the host whose
        # repositories render as an unclickable folder path.
        if not django_apps.is_installed("toto.ambrosia"):
            self.skipTest("no workspace app on this host")

        from toto.ambrosia.models import Workspace

        workspace = Workspace.objects.create(
            name="Analysis", owner=self.user, kind="python",
            bucket=self.bucket, root_directory=self.root)

        repo = self.make_repo()
        surface = pages._surface_link(repo)

        self.assertEqual(surface["kind"], "workspace")
        self.assertEqual(surface["label"], workspace.name)
        self.assertTrue(surface["url"], "a workspace repo must be clickable")
        self.assertIn(workspace.slug, surface["url"])

    def test_a_plain_folder_repo_has_no_dead_link(self):
        repo = self.make_repo()
        surface = pages._surface_link(repo)
        self.assertEqual(surface["kind"], "folder")
        self.assertEqual(surface["url"], "")


class LandingPageTests(GitvaultTestCase):
    def setUp(self):
        super().setUp()
        # Every page render goes through PageProcessor, which answers 404
        # without an active Platform — the row a deployed host always has.
        from toto.core.models import Platform

        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        self.client.force_login(self.user)

    def test_the_page_offers_local_repositories(self):
        self.make_repo()
        response = self.client.get(reverse("gitvault:index"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Local repositories", body)

    @override_settings(GITEA_ENABLED=False)
    def test_without_gitea_there_is_no_codebase_card(self):
        # The shape a federation child always renders: local repositories and
        # nothing else. The Codebase card links a sidecar such a host cannot
        # run, so offering it would be a link to a 404.
        response = self.client.get(reverse("gitvault:index"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Codebase", response.content.decode())

    @override_settings(GITEA_ENABLED=True, GITEA_URL="/gitea/")
    def test_with_gitea_both_choices_are_offered(self):
        response = self.client.get(reverse("gitvault:index"))
        body = response.content.decode()
        self.assertIn("Codebase", body)
        self.assertIn("Local repositories", body)

    def test_every_listed_repo_is_handed_a_read_only_context(self):
        # Read-only by contract: the listing offers History and nothing else,
        # and each row's ctx carries readOnly so the modal hides Restore.
        # (The shared modal host defines every method — asserting those strings
        # are absent would only be testing that the include is missing.)
        self.make_repo()
        response = self.client.get(reverse("gitvault:index"))
        body = response.content.decode()

        self.assertIn("openHistory", body)
        repo_map = response.context["repo_map"]
        self.assertTrue(repo_map, "a repo was created, so the map cannot be empty")
        for ctx in repo_map.values():
            self.assertTrue(ctx["readOnly"])
