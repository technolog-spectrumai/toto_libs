"""The dashboard landing page: the local repositories, and where each links back to.

The list is READ-ONLY by contract — it says what exists and sends you to the
surface that owns it. Committing, merging, branching and switching all happen
in the workspace or the editor, where the files are.
"""

from django.apps import apps as django_apps
from django.test import override_settings
from django.urls import reverse

from toto.repo import pages

from .base import RepoTestCase


class SurfaceLinkTests(RepoTestCase):
    def test_a_workspace_repo_links_to_its_room(self):
        # Workspace attribution was deleted on the premise that the app could
        # never run where the workspaces live. It can now, so a repo rooted on
        # a workspace folder must name the workspace and link to its room —
        # otherwise the host that owns the labs is exactly the host whose
        # repositories render as an unclickable folder path.
        if not django_apps.is_installed("toto.ambrosia"):
            self.skipTest("no workspace app on this host")
        # ambrosia is NOT enough on its own, and that stopped being a detail on
        # 2026-09-01. `_surface_link` links a repo to its workspace ROOM, and a
        # room belongs to a LANGUAGE app — toto.texlab or toto.dracena — which
        # went to the placidia repository. zenobia keeps ambrosia for version
        # control over workspace folders and installs neither language, so
        # `_workspace_namespace` correctly returns nothing and the repo renders
        # as a folder. Asserting a room here would demand a link to a page this
        # host does not serve.
        if not any(django_apps.is_installed(app)
                   for app in ("toto.texlab", "toto.dracena")):
            self.skipTest("no language app, so a workspace has no room to link to")

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


class LandingPageTests(RepoTestCase):
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
        response = self.client.get(reverse("repo:index"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Local repositories", body)

    @override_settings(GITEA_ENABLED=True, GITEA_URL="/gitea/")
    def test_the_page_never_speaks_of_a_hosted_forge(self):
        # Even with a sidecar configured. This app renders local repositories
        # and nothing else: the hosted-code card belongs to toto.gitea, whose
        # own page owns it, and the split exists precisely because the host
        # running one of the two does not run the other.
        self.make_repo()
        response = self.client.get(reverse("repo:index"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Local repositories", body)
        self.assertNotIn("Codebase", body)

    def test_the_page_renders_without_the_gitea_app(self):
        """The break the split was one template line away from shipping.

        `_git_ui.html` used to reverse the Gitea picker unconditionally, so on
        a host without that app EVERY page including this partial — the
        editors, the decks, the workspaces, and this one — died with
        NoReverseMatch. The guard is doubly conditional now; this asserts the
        page survives its absence.
        """
        if django_apps.is_installed("toto.gitea"):
            self.skipTest("this host installs the Gitea half too")
        self.make_repo()
        response = self.client.get(reverse("repo:index"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("REPO_REMOTE_PICKER_URL", response.content.decode())

    def test_every_listed_repo_is_handed_a_read_only_context(self):
        # Read-only by contract: the listing offers History and nothing else,
        # and each row's ctx carries readOnly so the modal hides Restore.
        # (The shared modal host defines every method — asserting those strings
        # are absent would only be testing that the include is missing.)
        self.make_repo()
        response = self.client.get(reverse("repo:index"))
        body = response.content.decode()

        self.assertIn("openHistory", body)
        repo_map = response.context["repo_map"]
        self.assertTrue(repo_map, "a repo was created, so the map cannot be empty")
        for ctx in repo_map.values():
            self.assertTrue(ctx["readOnly"])
