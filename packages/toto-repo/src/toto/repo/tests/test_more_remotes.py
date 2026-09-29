"""The two halves meeting: a local repository pushing to the platform's forge.

`toto.repo` never names `toto.gitea`; the forge's app claims its own URLs
through `repo.remotes` and hands over the user's token, which git receives as a
per-invocation `http.extraHeader` and never writes to `.git/config`. These run
that seam for real against a local bare repository standing in for the forge:
the push and fetch really happen, the only thing mocked is the forge's admin
API that mints the token (`gitea.client.ensure_account`).

Also here: the doors' remaining refusals and the repository list's links.
"""

import base64
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock, skip

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse

from toto.repo import git_cli, pages, runner, services
from toto.repo.models import GitRun

from .base import RepoTestCase

FORGE = {"GITEA_ENABLED": True, "GITEA_INTERNAL_URL": "http://gitea:3000",
         "GITEA_SVC_PASSWORD": "svc-pw"}
FORGE_URL = "http://gitea:3000/alice/notes.git"

#: pages.index catches only git_cli.GitError around head_branch(), but a repo
#: row whose worktree was never materialised (init queued on a worker, or a
#: dispatch that failed and was recorded) has no worktree DIRECTORY, and
#: subprocess raises FileNotFoundError for the missing cwd — so /repo/ answers
#: 500 to everyone who can see that repository.
#: Every repo door authorises with `directory.user_can_access`, which answers
#: True for ANY signed-in user when the folder has no whitelist — the vault's
#: own read rule (access.may_read: owner, public, bucket owner, ACL member)
#: grants a stranger nothing there. So any user past REPO_ACCESS (staff by
#: default) can status, commit, checkout, merge and restore a repository over
#: another person's un-whitelisted folder, and checkout/restore import the
#: result back over the owner's vault files.
OPEN_FOLDER_BUG = ("suspected production bug: repo/views.py:_get_repo authorises with "
                   "VaultDirectory.user_can_access, which admits any user to a folder with "
                   "no whitelist; a staff stranger restores the owner's files")

UNMATERIALISED_BUG = ("production bug: repo/pages.py:105 catches GitError only; a repo "
                      "without a worktree directory raises FileNotFoundError and 500s /repo/")


def _header(username, token):
    b64 = base64.b64encode(f"{username}:{token}".encode()).decode()
    return f"http.extraHeader=Authorization: Basic {b64}"


def _needs_gitea(test):
    def wrapped(self, *args, **kwargs):
        if not django_apps.is_installed("toto.gitea"):
            self.skipTest("the forge's app is not installed here")
        return test(self, *args, **kwargs)

    wrapped.__name__ = test.__name__
    wrapped.__doc__ = test.__doc__
    return wrapped


class ForgeRemoteTests(RepoTestCase):
    """A repo whose recorded origin is on the platform's forge, whose actual
    git origin is a local bare repository (git cannot reach gitea:3000 here)."""

    def setUp(self):
        super().setUp()
        self.repo = self.make_repo()
        self.bare = Path(tempfile.mkdtemp(prefix="repo-forge-"))
        self.addCleanup(shutil.rmtree, self.bare, True)
        subprocess.run(["git", "init", "--bare", "-q", str(self.bare)], check=True)

    def point_at(self, url):
        # The URL a person connected — what providers are asked about...
        self.repo.remote_url = url
        self.repo.save(update_fields=["remote_url"])
        # ...and where git actually goes. A local path is refused by
        # connect_remote on purpose, so it is set underneath it.
        git_cli.remote_set(self.repo.worktree, str(self.bare))

    def account(self, token="tok-123"):
        from toto.gitea.models import GiteaAccount

        account = GiteaAccount(user=self.user, username="alice")
        account.set_token(token)
        return account

    def git_calls(self, spy):
        return [call.args[0] for call in spy.call_args_list]

    @_needs_gitea
    @override_settings(**FORGE)
    def test_a_push_to_the_forge_carries_the_users_own_token_in_the_header(self):
        self.point_at(FORGE_URL)
        with mock.patch("toto.gitea.client.ensure_account", return_value=self.account()), \
                mock.patch("toto.repo.git_cli.run_git", wraps=git_cli.run_git) as spy:
            services.run_push(self.repo, self.user)
        push = next(args for args in self.git_calls(spy) if "push" in args)
        self.assertEqual(push[:2], ["-c", _header("alice", "tok-123")])
        # The push really happened...
        branches = subprocess.run(["git", "--git-dir", str(self.bare), "branch"],
                                  capture_output=True, text=True).stdout
        self.assertIn("main", branches)
        # ...and the token was never written anywhere git keeps.
        config = (Path(self.repo.worktree) / ".git" / "config").read_text()
        self.assertNotIn("tok-123", config)
        self.assertNotIn("extraHeader", config)

    @_needs_gitea
    @override_settings(**FORGE)
    def test_a_push_anywhere_else_carries_no_credentials(self):
        self.point_at("https://github.com/alice/notes.git")
        with mock.patch("toto.gitea.client.ensure_account") as ensure, \
                mock.patch("toto.repo.git_cli.run_git", wraps=git_cli.run_git) as spy:
            services.run_push(self.repo, self.user)
        ensure.assert_not_called()
        push = next(args for args in self.git_calls(spy) if "push" in args)
        self.assertEqual(push[0], "push")

    @_needs_gitea
    @override_settings(**{**FORGE, "GITEA_ENABLED": False})
    def test_with_the_sidecar_off_even_its_own_url_is_pushed_verbatim(self):
        self.point_at(FORGE_URL)
        with mock.patch("toto.gitea.client.ensure_account") as ensure, \
                mock.patch("toto.repo.git_cli.run_git", wraps=git_cli.run_git) as spy:
            services.run_push(self.repo, self.user)
        ensure.assert_not_called()
        self.assertNotIn("-c", next(a for a in self.git_calls(spy) if "push" in a))

    @_needs_gitea
    @override_settings(**FORGE)
    def test_a_pull_from_the_forge_fetches_with_the_token_and_imports_the_result(self):
        self.point_at(FORGE_URL)
        with mock.patch("toto.gitea.client.ensure_account", return_value=self.account()):
            services.run_push(self.repo, self.user)
        with mock.patch("toto.gitea.client.ensure_account",
                        return_value=self.account("tok-456")), \
                mock.patch("toto.repo.git_cli.run_git", wraps=git_cli.run_git) as spy:
            result = services.run_pull(self.repo, self.user)
        fetch = next(args for args in self.git_calls(spy) if "fetch" in args)
        self.assertEqual(fetch[:2], ["-c", _header("alice", "tok-456")])
        self.assertIn("import_summary", result)

    @_needs_gitea
    @override_settings(**FORGE)
    def test_a_forge_that_cannot_mint_a_token_fails_the_run_loudly_and_refunds_it(self):
        from toto.gitea.errors import GiteaError
        from toto.repo.dispatch import create_git_run

        self.point_at(FORGE_URL)
        run = create_git_run(self.user, self.repo, "push")
        with mock.patch("toto.gitea.client.ensure_account",
                        side_effect=GiteaError("gitea token creation failed: nope")), \
                mock.patch("toto.quota.charge.refund_for") as refund:
            runner.execute_git_run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, GitRun.FAILED)
        self.assertIn("gitea token creation failed", run.stderr)
        refund.assert_called_once()
        self.assertEqual(refund.call_args.args[:3], ("repo.GitRun", run.pk, "repo.run"))

    @_needs_gitea
    @override_settings(**FORGE)
    def test_the_status_panel_names_who_authenticates_the_remote(self):
        self.point_at(FORGE_URL)
        self.assertEqual(services.repo_status(self.repo)["remote"]["authenticated_by"], "Gitea")
        self.point_at("https://github.com/alice/notes.git")
        self.assertEqual(services.repo_status(self.repo)["remote"]["authenticated_by"], "")

    @_needs_gitea
    @override_settings(**FORGE)
    def test_connecting_says_whether_the_platform_will_authenticate(self):
        self.client.force_login(self.user)
        ours = self.client.post(reverse("repo:connect", args=[self.repo.pk]),
                                {"url": FORGE_URL}).json()
        self.assertEqual(ours, {"url": FORGE_URL, "authenticated_by": "Gitea"})
        theirs = self.client.post(reverse("repo:connect", args=[self.repo.pk]),
                                  {"url": "git@github.com:alice/notes.git"}).json()
        self.assertEqual(theirs["authenticated_by"], "")

    def test_a_run_that_already_finished_is_not_run_again(self):
        from toto.repo.dispatch import create_git_run

        self.point_at("https://github.com/alice/notes.git")
        run = create_git_run(self.user, self.repo, "push")
        GitRun.objects.filter(pk=run.pk).update(status=GitRun.FAILED, stderr="swept")
        with mock.patch("toto.repo.services.run_push") as push:
            runner.execute_git_run(run.pk)
        push.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.stderr, "swept")


class AuthHeaderTests(RepoTestCase):
    def test_the_header_is_basic_auth_of_user_and_token(self):
        self.assertEqual(git_cli.auth_config_args("alice", "tok"),
                         ["-c", "http.extraHeader=Authorization: Basic YWxpY2U6dG9r"])

    def test_a_username_without_a_token_sends_no_header(self):
        repo = self.make_repo()
        with mock.patch("toto.repo.git_cli.run_git") as run_git:
            git_cli.push(repo.worktree, "main", "alice", "")
        self.assertEqual(run_git.call_args.args[0], ["push", "-u", "origin", "main"])


class DoorRefusalTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.repo = self.make_repo()

    def test_a_stranger_cannot_init_a_repository_on_my_whitelisted_folder(self):
        from toto.vault.models import VaultDirectory

        stranger = User.objects.create_user("mallory", password="pw", is_staff=True)
        spare = VaultDirectory.objects.create(bucket=self.bucket, owner=self.user,
                                              name="spare")
        spare.allowed_users.add(self.user)   # the folder is shared with its owner only
        self.client.force_login(stranger)
        response = self.client.post(reverse("repo:init", args=[spare.pk]))
        self.assertEqual(response.status_code, 404)
        from toto.repo.models import GitRepo

        self.assertFalse(GitRepo.objects.filter(directory=spare).exists())

    def test_resolutions_must_be_an_object(self):
        response = self.client.post(reverse("repo:merge", args=[self.repo.pk]),
                                    {"branch": "main", "resolutions": "[1, 2]"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("must be an object", response.json()["error"])

    def test_pulling_without_a_remote_is_refused(self):
        response = self.client.post(reverse("repo:pull", args=[self.repo.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(GitRun.objects.filter(repo=self.repo, op="pull").exists())

    def test_a_dispatch_that_fails_is_recorded_on_the_run_not_raised(self):
        self.repo.remote_url = "https://github.com/alice/notes.git"
        self.repo.save(update_fields=["remote_url"])
        with mock.patch("toto.repo.views.dispatch_git_run",
                        side_effect=RuntimeError("broker is down")), \
                mock.patch("toto.quota.charge.refund_for"):
            response = self.client.post(reverse("repo:push", args=[self.repo.pk]))
        self.assertEqual(response.status_code, 200)
        run = GitRun.objects.get(pk=response.json()["run_id"])
        self.assertEqual(run.status, GitRun.FAILED)
        self.assertIn("broker is down", run.stderr)

    def test_someone_elses_run_is_a_404(self):
        from toto.repo.dispatch import create_git_run

        run = create_git_run(self.user, self.repo, "init")
        self.root.allowed_users.add(self.user)
        stranger = User.objects.create_user("mallory", password="pw", is_staff=True)
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(reverse("repo:run_status", args=[run.pk])).status_code,
                         404)

    def test_a_branch_needs_a_name_to_be_deleted(self):
        response = self.client.post(reverse("repo:branch_delete", args=[self.repo.pk]),
                                    {"name": "  "})
        self.assertEqual(response.status_code, 400)

    def test_a_branch_name_with_spaces_is_refused(self):
        response = self.client.post(reverse("repo:branch_create", args=[self.repo.pk]),
                                    {"name": "two words"})
        self.assertEqual(response.status_code, 400)

    @override_settings(REPO_WORKSPACES_ONLY=True)
    def test_a_host_that_versions_workspaces_only_refuses_a_plain_folder(self):
        from toto.vault.models import VaultDirectory

        plain = VaultDirectory.objects.create(bucket=self.bucket, owner=self.user,
                                              name="plain")
        response = self.client.post(reverse("repo:init", args=[plain.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("workspace folders only", response.json()["error"])


class OtherPeoplesFoldersTests(RepoTestCase):
    """Git on someone else's folder, by staff who do not own it."""

    @skip(OPEN_FOLDER_BUG)
    def test_staff_who_do_not_own_a_folder_cannot_rewrite_its_files_through_git(self):
        repo = self.make_repo()
        first = services.repo_status(repo)   # materialised, one commit
        self.assertEqual(first["branch"], "main")
        initial = git_cli.run_git(["rev-parse", "HEAD"], cwd=repo.worktree).stdout.strip()
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"owner's second draft\n")
        services.commit(repo, self.user, "second draft")
        intruder = User.objects.create_user("mallory", password="pw", is_staff=True)
        self.client.force_login(intruder)
        response = self.client.post(reverse("repo:restore", args=[repo.pk, initial]))
        self.assertEqual(response.status_code, 404)
        self.f_notes.refresh_from_db()
        with self.f_notes.file.open("rb") as fh:
            self.assertEqual(fh.read(), b"owner's second draft\n")


class RepositoryListTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        # The page 404s without an active Platform, the row every host has.
        from toto.core.models import Platform

        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)

    def test_below_the_gate_the_list_is_refused(self):
        clerk = User.objects.create_user("clerk", password="pw")
        self.client.force_login(clerk)
        self.assertEqual(self.client.get(reverse("repo:index")).status_code, 403)

    @skip(UNMATERIALISED_BUG)
    def test_a_repository_still_initialising_is_listed_without_a_branch(self):
        repo = services.create_repo(self.root, self.user)   # no worktree yet
        self.client.force_login(self.user)
        rows = self.client.get(reverse("repo:index")).context["repos"]
        self.assertEqual([(r["repo"].pk, r["branch"]) for r in rows], [(repo.pk, "")])

    def test_a_repository_holding_a_document_links_to_the_writer(self):
        if not django_apps.is_installed("toto.cyprian"):
            self.skipTest("no writer on this host")
        doc = self._file("paper.html", b"<p>draft</p>", self.sub, file_type="html")
        repo = self.make_repo()
        self.assertEqual(pages._surface_link(repo), {
            "label": "paper.html", "kind": "document",
            "url": reverse("cyprian:edit", args=[doc.pk])})

    def test_a_workspace_whose_lab_is_not_installed_is_a_plain_folder(self):
        if not django_apps.is_installed("toto.ambrosia"):
            self.skipTest("no workspaces on this host")
        from toto.ambrosia import registry
        from toto.ambrosia.models import Workspace

        Workspace.objects.create(name="Analysis", owner=self.user, kind="python",
                                 bucket=self.bucket, root_directory=self.root)
        repo = self.make_repo()
        with mock.patch.dict(registry._BY_KIND, clear=True):
            surface = pages._surface_link(repo)
        self.assertEqual((surface["kind"], surface["url"]), ("folder", ""))

    def test_a_stranger_does_not_see_my_repository_listed(self):
        self.make_repo()
        self.root.allowed_users.add(self.user)
        stranger = User.objects.create_user("mallory", password="pw", is_staff=True)
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(reverse("repo:index")).context["repos"], [])
