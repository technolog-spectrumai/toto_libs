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
        intruder = User.objects.create_user("mallory", "m@example.com", "pw")
        self.client.force_login(intruder)
        resp = self.client.get(reverse("gitvault:status", args=[repo.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_push_requires_remote(self):
        repo = self.make_repo()
        resp = self.client.post(reverse("gitvault:push", args=[repo.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not connected", resp.json()["error"])

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

    def test_file_context(self):
        repo = self.make_repo()
        resp = self.client.get(reverse("gitvault:file_context", args=[self.f_deep.pk]))
        data = resp.json()
        self.assertTrue(data["in_repo"])
        self.assertEqual(data["repo_pk"], repo.pk)
        self.assertIn("commit", data["urls"])

        outside = self._file("loose.txt", b"x", None)
        resp = self.client.get(reverse("gitvault:file_context", args=[outside.pk]))
        self.assertFalse(resp.json()["in_repo"])

    def test_anonymous_redirected(self):
        repo = self.make_repo()
        self.client.logout()
        resp = self.client.get(reverse("gitvault:status", args=[repo.pk]))
        self.assertEqual(resp.status_code, 302)
