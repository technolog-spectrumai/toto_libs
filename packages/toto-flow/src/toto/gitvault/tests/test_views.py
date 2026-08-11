"""Endpoint contracts: happy paths, permissions, busy/conflict statuses."""

from django.contrib.auth.models import User
from django.urls import reverse

from toto.gitvault import services, sync
from toto.vault.models import VaultFile

from .base import GitvaultTestCase


class ViewTests(GitvaultTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_init_and_status(self):
        resp = self.client.post(reverse("gitvault:init", args=[self.root.pk]))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        repo_pk = payload["repo_pk"]
        # init is a background GitRun now — celery-less tests run it inline,
        # so the run is already terminal and the repo materialized.
        run_resp = self.client.get(
            reverse("gitvault:run_status", args=[payload["run_id"]])
        ).json()
        self.assertEqual(run_resp["op"], "init")
        self.assertEqual(run_resp["status"], "success")
        resp = self.client.get(reverse("gitvault:status", args=[repo_pk]))
        data = resp.json()
        self.assertEqual(data["branch"], "main")
        self.assertEqual(data["dirty"], [])
        self.assertIn("secret.txt", data["encrypted_skipped"])

    def test_init_nested_rejected(self):
        self.client.post(reverse("gitvault:init", args=[self.root.pk]))
        resp = self.client.post(reverse("gitvault:init", args=[self.sub.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("nested", resp.json()["error"])

    def test_commit_history_and_detail(self):
        repo = self.make_repo()
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"v2\n")
        resp = self.client.post(
            reverse("gitvault:commit", args=[repo.pk]), {"message": "v2"}
        )
        self.assertEqual(resp.status_code, 200)
        sha = resp.json()["sha"]

        graph = self.client.get(reverse("gitvault:history", args=[repo.pk])).json()
        self.assertEqual(len(graph["nodes"]), 2)

        detail = self.client.get(
            reverse("gitvault:commit_detail", args=[repo.pk, sha])
        ).json()
        self.assertEqual(detail["message"], "v2")
        self.assertEqual(detail["files"][0]["path"], "notes.txt")

    def test_commit_empty_message_400(self):
        repo = self.make_repo()
        resp = self.client.post(reverse("gitvault:commit", args=[repo.pk]), {"message": ""})
        self.assertEqual(resp.status_code, 400)

    def test_branch_checkout_merge_conflict_409(self):
        repo = self.make_repo()
        self.client.post(
            reverse("gitvault:branch_create", args=[repo.pk]),
            {"name": "feature", "checkout": "0"},
        )
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"main\n")
        self.client.post(reverse("gitvault:commit", args=[repo.pk]), {"message": "m"})
        self.client.post(reverse("gitvault:checkout", args=[repo.pk]), {"branch": "feature"})
        with VaultFile.objects.get(pk=self.f_notes.pk).file.open("wb") as fh:
            fh.write(b"feature\n")
        self.client.post(reverse("gitvault:commit", args=[repo.pk]), {"message": "f"})
        self.client.post(reverse("gitvault:checkout", args=[repo.pk]), {"branch": "main"})

        resp = self.client.post(reverse("gitvault:merge", args=[repo.pk]), {"branch": "feature"})
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertTrue(data["aborted"])
        self.assertEqual(data["conflicts"], ["notes.txt"])

    def test_repo_busy_409(self):
        repo = self.make_repo()
        with sync.repo_lock(repo):
            resp = self.client.get(reverse("gitvault:status", args=[repo.pk]))
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(resp.json()["busy"])

    def test_permission_denied_other_user(self):
        repo = self.make_repo()
        self.root.allowed_users.add(self.user)  # restrict to owner
        # Staff, so the GITVAULT_ACCESS gate passes and the directory
        # whitelist is what refuses — still 404, existence hidden even from
        # other staff.
        intruder = User.objects.create_user("mallory", "m@example.com", "pw",
                                            is_staff=True)
        self.client.force_login(intruder)
        resp = self.client.get(reverse("gitvault:status", args=[repo.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_push_requires_remote(self):
        repo = self.make_repo()
        resp = self.client.post(reverse("gitvault:push", args=[repo.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no remote", resp.json()["error"])

    def test_connect_requires_gitea_enabled(self):
        repo = self.make_repo()
        with self.settings(GITEA_ENABLED=False):
            resp = self.client.post(reverse("gitvault:connect", args=[repo.pk]))
        self.assertEqual(resp.status_code, 400)

    def test_connect_attaches_existing_repo(self):
        from unittest import mock
        repo = self.make_repo()
        with self.settings(GITEA_ENABLED=True), \
                mock.patch("toto.gitvault.gitea_client.ensure_account") as ea, \
                mock.patch("toto.gitvault.gitea_client.create_repo") as cr:
            ea.return_value = mock.Mock(username="alice")
            resp = self.client.post(
                reverse("gitvault:connect", args=[repo.pk]),
                {"existing": "team/shared"},
            )
        self.assertEqual(resp.status_code, 200)
        cr.assert_not_called()  # attach, don't create
        repo.refresh_from_db()
        self.assertEqual((repo.remote_owner, repo.remote_name), ("team", "shared"))

    def test_gitea_repos_endpoint(self):
        from unittest import mock
        from toto.gitvault.models import GiteaAccount
        # no account yet → empty list
        with self.settings(GITEA_ENABLED=True):
            data = self.client.get(reverse("gitvault:gitea_repos")).json()
        self.assertEqual(data, {"enabled": True, "repos": []})
        # with an account → lists via gitea_client
        acct = GiteaAccount.objects.create(user=self.user, username="alice")
        acct.set_token("t"); acct.save()
        with self.settings(GITEA_ENABLED=True), \
                mock.patch("toto.gitvault.gitea_client.list_repos",
                           return_value=[{"full_name": "alice/notes", "owner": "alice", "name": "notes"}]):
            data = self.client.get(reverse("gitvault:gitea_repos")).json()
        self.assertEqual(data["repos"][0]["full_name"], "alice/notes")

    def test_gitea_repos_disabled(self):
        with self.settings(GITEA_ENABLED=False):
            data = self.client.get(reverse("gitvault:gitea_repos")).json()
        self.assertEqual(data, {"enabled": False, "repos": []})

    def test_anonymous_redirected(self):
        repo = self.make_repo()
        self.client.logout()
        resp = self.client.get(reverse("gitvault:status", args=[repo.pk]))
        self.assertEqual(resp.status_code, 302)


class RestoreViewTests(GitvaultTestCase):
    """The restore endpoint, and the vault following the restored tree."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_restore_updates_the_vault(self):
        repo = self.make_repo()
        old = self.client.get(
            reverse("gitvault:history", args=[repo.pk])).json()["nodes"][0]["id"]

        with self.f_notes.file.open("wb") as fh:
            fh.write(b"v2\n")
        self.client.post(reverse("gitvault:commit", args=[repo.pk]), {"message": "v2"})

        resp = self.client.post(reverse("gitvault:restore", args=[repo.pk, old]))
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertIn("notes.txt", data["import_summary"]["updated"])

        # The vault row carries the restored bytes again.
        self.f_notes.refresh_from_db()
        with self.f_notes.file.open("rb") as fh:
            self.assertEqual(fh.read(), b"hello notes\n")

        # History grew — nothing was reset away.
        graph = self.client.get(reverse("gitvault:history", args=[repo.pk])).json()
        self.assertEqual(len(graph["nodes"]), 3)

    def test_restore_with_uncommitted_changes_400(self):
        repo = self.make_repo()
        old = self.client.get(
            reverse("gitvault:history", args=[repo.pk])).json()["nodes"][0]["id"]
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"dirty\n")
        resp = self.client.post(reverse("gitvault:restore", args=[repo.pk, old]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("uncommitted", resp.json()["error"])


class CustomRemoteTests(GitvaultTestCase):
    """A remote address of the user's own — no Gitea anywhere in the path."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_connect_custom_url_without_gitea(self):
        repo = self.make_repo()
        with self.settings(GITEA_ENABLED=False):
            resp = self.client.post(
                reverse("gitvault:connect", args=[repo.pk]),
                {"url": "https://example.com/me/repo.git"})
        self.assertEqual(resp.status_code, 200, resp.content)
        repo.refresh_from_db()
        self.assertEqual(repo.remote_url, "https://example.com/me/repo.git")
        self.assertTrue(repo.remote_connected)
        self.assertFalse(repo.uses_gitea)

        # And status reports it, so the panel can render push/pull.
        status = self.client.get(reverse("gitvault:status", args=[repo.pk])).json()
        self.assertTrue(status["remote"]["connected"])
        self.assertTrue(status["remote"]["custom"])
        self.assertEqual(status["remote"]["clone_url"],
                         "https://example.com/me/repo.git")

    def test_connect_rejects_a_non_url(self):
        repo = self.make_repo()
        resp = self.client.post(
            reverse("gitvault:connect", args=[repo.pk]), {"url": "not a url"})
        self.assertEqual(resp.status_code, 400)

    def test_custom_url_replaces_a_gitea_pair(self):
        repo = self.make_repo()
        repo.remote_owner, repo.remote_name = "alice", "old"
        repo.save(update_fields=["remote_owner", "remote_name"])
        self.client.post(
            reverse("gitvault:connect", args=[repo.pk]),
            {"url": "https://example.com/me/new.git"})
        repo.refresh_from_db()
        self.assertEqual(repo.remote_owner, "")
        self.assertEqual(repo.remote_name, "")
        self.assertEqual(repo.remote_url, "https://example.com/me/new.git")

    def test_init_takes_a_default_branch(self):
        from toto.vault.models import VaultDirectory

        other = VaultDirectory.objects.create(
            name="branched", bucket=self.bucket, parent=None, owner=self.user)
        resp = self.client.post(
            reverse("gitvault:init", args=[other.pk]), {"default_branch": "trunk"})
        self.assertEqual(resp.status_code, 200, resp.content)
        from toto.gitvault.models import GitRepo
        repo = GitRepo.objects.get(pk=resp.json()["repo_pk"])
        self.assertEqual(repo.default_branch, "trunk")
        # The run executed inline (no celery in tests): HEAD is on the branch.
        from toto.gitvault import git_cli
        self.assertEqual(git_cli.head_branch(repo.worktree), "trunk")

    def test_init_rejects_a_spaced_branch(self):
        from toto.vault.models import VaultDirectory

        other = VaultDirectory.objects.create(
            name="badbranch", bucket=self.bucket, parent=None, owner=self.user)
        resp = self.client.post(
            reverse("gitvault:init", args=[other.pk]), {"default_branch": "a b"})
        self.assertEqual(resp.status_code, 400)


class BranchDeleteTests(GitvaultTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_delete_a_merged_branch(self):
        repo = self.make_repo()
        self.client.post(reverse("gitvault:branch_create", args=[repo.pk]),
                         {"name": "side", "checkout": "0"})
        resp = self.client.post(reverse("gitvault:branch_delete", args=[repo.pk]),
                                {"name": "side"})
        self.assertEqual(resp.status_code, 200, resp.content)
        branches = self.client.get(
            reverse("gitvault:branches", args=[repo.pk])).json()["branches"]
        self.assertNotIn("side", branches)

    def test_the_current_branch_is_refused(self):
        repo = self.make_repo()
        resp = self.client.post(reverse("gitvault:branch_delete", args=[repo.pk]),
                                {"name": "main"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("current", resp.json()["error"])

    def test_an_unmerged_branch_is_refused_by_git(self):
        repo = self.make_repo()
        self.client.post(reverse("gitvault:branch_create", args=[repo.pk]),
                         {"name": "wip", "checkout": "1"})
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"wip work\n")
        self.client.post(reverse("gitvault:commit", args=[repo.pk]), {"message": "wip"})
        self.client.post(reverse("gitvault:checkout", args=[repo.pk]), {"branch": "main"})

        resp = self.client.post(reverse("gitvault:branch_delete", args=[repo.pk]),
                                {"name": "wip"})
        # git's own -d refusal comes through as a 400 with its message.
        self.assertEqual(resp.status_code, 400)
        self.assertIn("wip", resp.json()["error"])


class MergeResolutionViewTests(GitvaultTestCase):
    """409 with details → resolutions → 200, and the vault follows."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.repo = self.make_repo()
        # Conflict: notes.txt diverges on main and feature.
        self.client.post(reverse("gitvault:branch_create", args=[self.repo.pk]),
                         {"name": "feature", "checkout": "1"})
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"feature side\n")
        self.client.post(reverse("gitvault:commit", args=[self.repo.pk]),
                         {"message": "feature"})
        self.client.post(reverse("gitvault:checkout", args=[self.repo.pk]),
                         {"branch": "main"})
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"main side\n")
        self.client.post(reverse("gitvault:commit", args=[self.repo.pk]),
                         {"message": "main"})

    def test_conflict_then_resolve_theirs(self):
        resp = self.client.post(reverse("gitvault:merge", args=[self.repo.pk]),
                                {"branch": "feature"})
        self.assertEqual(resp.status_code, 409)
        payload = resp.json()
        self.assertEqual(payload["conflicts"], ["notes.txt"])
        detail = payload["details"][0]
        self.assertEqual(detail["ours"], "main side\n")
        self.assertEqual(detail["theirs"], "feature side\n")

        import json
        resp = self.client.post(
            reverse("gitvault:merge", args=[self.repo.pk]),
            {"branch": "feature",
             "resolutions": json.dumps({"notes.txt": "theirs"})})
        self.assertEqual(resp.status_code, 200, resp.content)

        # The vault row carries the settled bytes.
        with self.f_notes.file.open("rb") as fh:
            self.assertEqual(fh.read(), b"feature side\n")

    def test_resolve_with_content(self):
        self.client.post(reverse("gitvault:merge", args=[self.repo.pk]),
                         {"branch": "feature"})
        import json
        resp = self.client.post(
            reverse("gitvault:merge", args=[self.repo.pk]),
            {"branch": "feature",
             "resolutions": json.dumps(
                 {"notes.txt": {"content": "hand merged\n"}})})
        self.assertEqual(resp.status_code, 200, resp.content)
        with self.f_notes.file.open("rb") as fh:
            self.assertEqual(fh.read(), b"hand merged\n")

    def test_malformed_resolutions_400(self):
        resp = self.client.post(
            reverse("gitvault:merge", args=[self.repo.pk]),
            {"branch": "feature", "resolutions": "not json"})
        self.assertEqual(resp.status_code, 400)
