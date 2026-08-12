"""Endpoint contracts: happy paths, permissions, busy/conflict statuses."""

from django.contrib.auth.models import User
from django.urls import reverse

from toto.repo import services, sync
from toto.vault.models import VaultFile

from .base import RepoTestCase


class ViewTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_init_and_status(self):
        resp = self.client.post(reverse("repo:init", args=[self.root.pk]))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        repo_pk = payload["repo_pk"]
        # init is a background GitRun now — celery-less tests run it inline,
        # so the run is already terminal and the repo materialized.
        run_resp = self.client.get(
            reverse("repo:run_status", args=[payload["run_id"]])
        ).json()
        self.assertEqual(run_resp["op"], "init")
        self.assertEqual(run_resp["status"], "success")
        resp = self.client.get(reverse("repo:status", args=[repo_pk]))
        data = resp.json()
        self.assertEqual(data["branch"], "main")
        self.assertEqual(data["dirty"], [])
        self.assertIn("secret.txt", data["encrypted_skipped"])

    def test_init_nested_rejected(self):
        self.client.post(reverse("repo:init", args=[self.root.pk]))
        resp = self.client.post(reverse("repo:init", args=[self.sub.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("nested", resp.json()["error"])

    def test_commit_history_and_detail(self):
        repo = self.make_repo()
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"v2\n")
        resp = self.client.post(
            reverse("repo:commit", args=[repo.pk]), {"message": "v2"}
        )
        self.assertEqual(resp.status_code, 200)
        sha = resp.json()["sha"]

        graph = self.client.get(reverse("repo:history", args=[repo.pk])).json()
        self.assertEqual(len(graph["nodes"]), 2)

        detail = self.client.get(
            reverse("repo:commit_detail", args=[repo.pk, sha])
        ).json()
        self.assertEqual(detail["message"], "v2")
        self.assertEqual(detail["files"][0]["path"], "notes.txt")

    def test_commit_empty_message_400(self):
        repo = self.make_repo()
        resp = self.client.post(reverse("repo:commit", args=[repo.pk]), {"message": ""})
        self.assertEqual(resp.status_code, 400)

    def test_branch_checkout_merge_conflict_409(self):
        repo = self.make_repo()
        self.client.post(
            reverse("repo:branch_create", args=[repo.pk]),
            {"name": "feature", "checkout": "0"},
        )
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"main\n")
        self.client.post(reverse("repo:commit", args=[repo.pk]), {"message": "m"})
        self.client.post(reverse("repo:checkout", args=[repo.pk]), {"branch": "feature"})
        with VaultFile.objects.get(pk=self.f_notes.pk).file.open("wb") as fh:
            fh.write(b"feature\n")
        self.client.post(reverse("repo:commit", args=[repo.pk]), {"message": "f"})
        self.client.post(reverse("repo:checkout", args=[repo.pk]), {"branch": "main"})

        resp = self.client.post(reverse("repo:merge", args=[repo.pk]), {"branch": "feature"})
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertTrue(data["aborted"])
        self.assertEqual(data["conflicts"], ["notes.txt"])

    def test_repo_busy_409(self):
        repo = self.make_repo()
        with sync.repo_lock(repo):
            resp = self.client.get(reverse("repo:status", args=[repo.pk]))
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(resp.json()["busy"])

    def test_permission_denied_other_user(self):
        repo = self.make_repo()
        self.root.allowed_users.add(self.user)  # restrict to owner
        # Staff, so the REPO_ACCESS gate passes and the directory
        # whitelist is what refuses — still 404, existence hidden even from
        # other staff.
        intruder = User.objects.create_user("mallory", "m@example.com", "pw",
                                            is_staff=True)
        self.client.force_login(intruder)
        resp = self.client.get(reverse("repo:status", args=[repo.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_push_requires_remote(self):
        repo = self.make_repo()
        resp = self.client.post(reverse("repo:push", args=[repo.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no remote", resp.json()["error"])

    def test_connect_requires_a_url(self):
        """There is one way to connect and it is a URL, so an empty POST is a
        400 rather than a no-Gitea-here message. The app has no other shape of
        remote to fall back to."""
        repo = self.make_repo()
        resp = self.client.post(reverse("repo:connect", args=[repo.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("remote URL", resp.json()["error"])

    def test_anonymous_redirected(self):
        repo = self.make_repo()
        self.client.logout()
        resp = self.client.get(reverse("repo:status", args=[repo.pk]))
        self.assertEqual(resp.status_code, 302)


class RestoreViewTests(RepoTestCase):
    """The restore endpoint, and the vault following the restored tree."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_restore_updates_the_vault(self):
        repo = self.make_repo()
        old = self.client.get(
            reverse("repo:history", args=[repo.pk])).json()["nodes"][0]["id"]

        with self.f_notes.file.open("wb") as fh:
            fh.write(b"v2\n")
        self.client.post(reverse("repo:commit", args=[repo.pk]), {"message": "v2"})

        resp = self.client.post(reverse("repo:restore", args=[repo.pk, old]))
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertIn("notes.txt", data["import_summary"]["updated"])

        # The vault row carries the restored bytes again.
        self.f_notes.refresh_from_db()
        with self.f_notes.file.open("rb") as fh:
            self.assertEqual(fh.read(), b"hello notes\n")

        # History grew — nothing was reset away.
        graph = self.client.get(reverse("repo:history", args=[repo.pk])).json()
        self.assertEqual(len(graph["nodes"]), 3)

    def test_restore_with_uncommitted_changes_400(self):
        repo = self.make_repo()
        old = self.client.get(
            reverse("repo:history", args=[repo.pk])).json()["nodes"][0]["id"]
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"dirty\n")
        resp = self.client.post(reverse("repo:restore", args=[repo.pk, old]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("uncommitted", resp.json()["error"])


class RemoteTests(RepoTestCase):
    """The origin. One field, one way to set it, and no credentials of our own."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_connect_sets_the_origin(self):
        repo = self.make_repo()
        resp = self.client.post(
            reverse("repo:connect", args=[repo.pk]),
            {"url": "https://example.com/me/repo.git"})
        self.assertEqual(resp.status_code, 200, resp.content)
        repo.refresh_from_db()
        self.assertEqual(repo.remote_url, "https://example.com/me/repo.git")
        self.assertTrue(repo.remote_connected)

        # And status reports it, so the panel can render push/pull. Nothing
        # here claims the URL, so the push carries no credentials — the panel
        # says so rather than letting a private remote fail unexplained.
        status = self.client.get(reverse("repo:status", args=[repo.pk])).json()
        self.assertTrue(status["remote"]["connected"])
        self.assertEqual(status["remote"]["url"],
                         "https://example.com/me/repo.git")
        self.assertEqual(status["remote"]["authenticated_by"], "")

    def test_connect_rejects_a_non_url(self):
        repo = self.make_repo()
        resp = self.client.post(
            reverse("repo:connect", args=[repo.pk]), {"url": "not a url"})
        self.assertEqual(resp.status_code, 400)

    def test_connect_replaces_the_previous_origin(self):
        """One origin, always. The pre-split model had a second, Gitea-shaped
        spelling beside this one and a rule about which won; a leftover half of
        it is how a push lands somewhere nobody expects."""
        repo = self.make_repo()
        self.client.post(reverse("repo:connect", args=[repo.pk]),
                         {"url": "https://example.com/me/old.git"})
        self.client.post(reverse("repo:connect", args=[repo.pk]),
                         {"url": "https://example.com/me/new.git"})
        repo.refresh_from_db()
        self.assertEqual(repo.remote_url, "https://example.com/me/new.git")

    def test_a_registered_provider_is_named_and_used(self):
        """The seam toto.gitea plugs into, exercised without toto.gitea.

        A provider claiming a URL supplies the credentials git is given, and
        status names it. That is the whole contract between the two apps, so it
        is tested here — on a host where the only claimant is this fixture.
        """
        from toto.repo import remotes

        provider = remotes.RemoteProvider(
            name="Fixture",
            claims=lambda url: url.startswith("https://forge.test/"),
            credentials=lambda url, user: ("alice", "s3cret"),
        )
        remotes.registry.register(provider)
        try:
            repo = self.make_repo()
            self.client.post(reverse("repo:connect", args=[repo.pk]),
                             {"url": "https://forge.test/alice/notes.git"})
            status = self.client.get(
                reverse("repo:status", args=[repo.pk])).json()
            self.assertEqual(status["remote"]["authenticated_by"], "Fixture")
            self.assertEqual(
                remotes.credentials_for("https://forge.test/alice/notes.git",
                                        self.user),
                ("alice", "s3cret"))
            # An unclaimed URL still gets the empty pair, not this provider's.
            self.assertEqual(
                remotes.credentials_for("https://elsewhere.test/x.git",
                                        self.user),
                ("", ""))
        finally:
            remotes.registry._providers.pop("Fixture", None)

    def test_a_broken_claimant_cannot_break_other_remotes(self):
        from toto.repo import remotes

        def explode(url):
            raise RuntimeError("provider is misconfigured")

        remotes.registry.register(remotes.RemoteProvider(
            name="Broken", claims=explode,
            credentials=lambda url, user: ("x", "y")))
        try:
            self.assertEqual(
                remotes.credentials_for("https://example.com/me/repo.git",
                                        self.user),
                ("", ""))
        finally:
            remotes.registry._providers.pop("Broken", None)

    def test_init_takes_a_default_branch(self):
        from toto.vault.models import VaultDirectory

        other = VaultDirectory.objects.create(
            name="branched", bucket=self.bucket, parent=None, owner=self.user)
        resp = self.client.post(
            reverse("repo:init", args=[other.pk]), {"default_branch": "trunk"})
        self.assertEqual(resp.status_code, 200, resp.content)
        from toto.repo.models import GitRepo
        repo = GitRepo.objects.get(pk=resp.json()["repo_pk"])
        self.assertEqual(repo.default_branch, "trunk")
        # The run executed inline (no celery in tests): HEAD is on the branch.
        from toto.repo import git_cli
        self.assertEqual(git_cli.head_branch(repo.worktree), "trunk")

    def test_init_rejects_a_spaced_branch(self):
        from toto.vault.models import VaultDirectory

        other = VaultDirectory.objects.create(
            name="badbranch", bucket=self.bucket, parent=None, owner=self.user)
        resp = self.client.post(
            reverse("repo:init", args=[other.pk]), {"default_branch": "a b"})
        self.assertEqual(resp.status_code, 400)


class BranchDeleteTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_delete_a_merged_branch(self):
        repo = self.make_repo()
        self.client.post(reverse("repo:branch_create", args=[repo.pk]),
                         {"name": "side", "checkout": "0"})
        resp = self.client.post(reverse("repo:branch_delete", args=[repo.pk]),
                                {"name": "side"})
        self.assertEqual(resp.status_code, 200, resp.content)
        branches = self.client.get(
            reverse("repo:branches", args=[repo.pk])).json()["branches"]
        self.assertNotIn("side", branches)

    def test_the_current_branch_is_refused(self):
        repo = self.make_repo()
        resp = self.client.post(reverse("repo:branch_delete", args=[repo.pk]),
                                {"name": "main"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("current", resp.json()["error"])

    def test_an_unmerged_branch_is_refused_by_git(self):
        repo = self.make_repo()
        self.client.post(reverse("repo:branch_create", args=[repo.pk]),
                         {"name": "wip", "checkout": "1"})
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"wip work\n")
        self.client.post(reverse("repo:commit", args=[repo.pk]), {"message": "wip"})
        self.client.post(reverse("repo:checkout", args=[repo.pk]), {"branch": "main"})

        resp = self.client.post(reverse("repo:branch_delete", args=[repo.pk]),
                                {"name": "wip"})
        # git's own -d refusal comes through as a 400 with its message.
        self.assertEqual(resp.status_code, 400)
        self.assertIn("wip", resp.json()["error"])


class MergeResolutionViewTests(RepoTestCase):
    """409 with details → resolutions → 200, and the vault follows."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.repo = self.make_repo()
        # Conflict: notes.txt diverges on main and feature.
        self.client.post(reverse("repo:branch_create", args=[self.repo.pk]),
                         {"name": "feature", "checkout": "1"})
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"feature side\n")
        self.client.post(reverse("repo:commit", args=[self.repo.pk]),
                         {"message": "feature"})
        self.client.post(reverse("repo:checkout", args=[self.repo.pk]),
                         {"branch": "main"})
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"main side\n")
        self.client.post(reverse("repo:commit", args=[self.repo.pk]),
                         {"message": "main"})

    def test_conflict_then_resolve_theirs(self):
        resp = self.client.post(reverse("repo:merge", args=[self.repo.pk]),
                                {"branch": "feature"})
        self.assertEqual(resp.status_code, 409)
        payload = resp.json()
        self.assertEqual(payload["conflicts"], ["notes.txt"])
        detail = payload["details"][0]
        self.assertEqual(detail["ours"], "main side\n")
        self.assertEqual(detail["theirs"], "feature side\n")

        import json
        resp = self.client.post(
            reverse("repo:merge", args=[self.repo.pk]),
            {"branch": "feature",
             "resolutions": json.dumps({"notes.txt": "theirs"})})
        self.assertEqual(resp.status_code, 200, resp.content)

        # The vault row carries the settled bytes.
        with self.f_notes.file.open("rb") as fh:
            self.assertEqual(fh.read(), b"feature side\n")

    def test_resolve_with_content(self):
        self.client.post(reverse("repo:merge", args=[self.repo.pk]),
                         {"branch": "feature"})
        import json
        resp = self.client.post(
            reverse("repo:merge", args=[self.repo.pk]),
            {"branch": "feature",
             "resolutions": json.dumps(
                 {"notes.txt": {"content": "hand merged\n"}})})
        self.assertEqual(resp.status_code, 200, resp.content)
        with self.f_notes.file.open("rb") as fh:
            self.assertEqual(fh.read(), b"hand merged\n")

    def test_malformed_resolutions_400(self):
        resp = self.client.post(
            reverse("repo:merge", args=[self.repo.pk]),
            {"branch": "feature", "resolutions": "not json"})
        self.assertEqual(resp.status_code, 400)
