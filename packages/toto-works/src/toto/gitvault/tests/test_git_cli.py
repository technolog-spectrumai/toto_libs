"""git_cli against the real git binary in throwaway directories (no DB)."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from toto.gitvault import git_cli


def fake_user(name="alice", email="alice@example.com"):
    return SimpleNamespace(
        username=name, email=email, get_full_name=lambda: "Alice Test"
    )


class GitCliTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gitvault-cli-")
        self.addCleanup(self.tmp.cleanup)
        self.wt = Path(self.tmp.name) / "wt"
        git_cli.init(self.wt)

    def _write(self, name, content):
        (self.wt / name).write_text(content)

    def _commit(self, msg="c"):
        git_cli.add_all(self.wt)
        return git_cli.commit(self.wt, msg, fake_user())

    def test_init_status_commit(self):
        self.assertEqual(git_cli.status(self.wt), [])
        self._write("a.txt", "one\n")
        entries = git_cli.status(self.wt)
        self.assertEqual([e["path"] for e in entries], ["a.txt"])
        sha = self._commit("first")
        self.assertEqual(len(sha), 40)
        self.assertEqual(git_cli.status(self.wt), [])
        self.assertEqual(git_cli.head_branch(self.wt), "main")

    def test_commit_identity_from_user(self):
        self._write("a.txt", "x")
        self._commit()
        log = git_cli.log_all(self.wt)
        self.assertEqual(log[0]["author"], "Alice Test")

    def test_branch_create_checkout_merge_clean(self):
        self._write("a.txt", "base\n")
        self._commit("base")
        git_cli.branch_create(self.wt, "feature")
        git_cli.checkout(self.wt, "feature")
        self._write("b.txt", "feature\n")
        self._commit("feature work")
        git_cli.checkout(self.wt, "main")
        sha = git_cli.merge(self.wt, "feature", fake_user())
        self.assertTrue((self.wt / "b.txt").exists())
        self.assertEqual(len(sha), 40)
        self.assertEqual(sorted(git_cli.branch_list(self.wt)), ["feature", "main"])

    def test_merge_conflict_aborts_and_lists_paths(self):
        self._write("a.txt", "base\n")
        self._commit("base")
        git_cli.branch_create(self.wt, "feature")
        self._write("a.txt", "main change\n")
        self._commit("main change")
        git_cli.checkout(self.wt, "feature")
        self._write("a.txt", "feature change\n")
        self._commit("feature change")
        git_cli.checkout(self.wt, "main")
        with self.assertRaises(git_cli.MergeConflict) as ctx:
            git_cli.merge(self.wt, "feature", fake_user())
        self.assertEqual(ctx.exception.paths, ["a.txt"])
        # merge aborted → clean tree, no conflict markers left behind
        self.assertEqual(git_cli.status(self.wt), [])
        self.assertEqual((self.wt / "a.txt").read_text(), "main change\n")

    def test_log_all_parents_and_refs(self):
        self._write("a.txt", "1")
        first = self._commit("first")
        self._write("a.txt", "2")
        second = self._commit("second")
        log = git_cli.log_all(self.wt)
        self.assertEqual([c["sha"] for c in log], [second, first])
        self.assertEqual(log[0]["parents"], [first])
        self.assertEqual(log[1]["parents"], [])
        refs = git_cli.refs(self.wt)
        self.assertEqual(refs["head"], "main")
        self.assertEqual(refs["branches"]["main"], second)

    def test_log_all_empty_repo(self):
        self.assertEqual(git_cli.log_all(self.wt), [])

    def test_show_commit_files(self):
        self._write("a.txt", "1")
        sha = self._commit("add a")
        detail = git_cli.show_commit(self.wt, sha)
        self.assertEqual(detail["sha"], sha)
        self.assertEqual(detail["message"], "add a")
        self.assertEqual(detail["files"], [{"status": "A", "path": "a.txt"}])

    def test_status_handles_rename(self):
        self._write("a.txt", "same content\n")
        self._commit("add")
        (self.wt / "a.txt").rename(self.wt / "b.txt")
        git_cli.add_all(self.wt)
        entries = git_cli.status(self.wt)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["state"], "R")
        self.assertEqual(entries[0]["path"], "b.txt")
        self.assertEqual(entries[0]["from"], "a.txt")

    def test_no_shell_injection_in_names(self):
        self._write("a.txt", "x")
        self._commit()
        # A valid ref name that would be dangerous under a shell — passed as argv.
        git_cli.branch_create(self.wt, "inject;echo-pwned")
        self.assertIn("inject;echo-pwned", git_cli.branch_list(self.wt))
