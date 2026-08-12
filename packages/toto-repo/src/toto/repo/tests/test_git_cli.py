"""git_cli against the real git binary in throwaway directories (no DB)."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from toto.repo import git_cli


def fake_user(name="alice", email="alice@example.com"):
    return SimpleNamespace(
        username=name, email=email, get_full_name=lambda: "Alice Test"
    )


class GitCliTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="repo-cli-")
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


class RestoreToTests(GitCliTests):
    """restore_to: a NEW commit whose tree equals the target's.

    The dangerous half is deletions — a file added AFTER the target commit must
    vanish from the tree, which `checkout <sha> -- .` cannot do. read-tree
    --reset can, which is why the implementation uses it.
    """

    def test_restore_recreates_and_deletes(self):
        self._write("a.txt", "one\n")
        old = self._commit("first")
        self._write("a.txt", "two\n")
        self._write("later.txt", "added after\n")
        self._commit("second")

        new = git_cli.restore_to(self.wt, old, fake_user())
        self.assertEqual((self.wt / "a.txt").read_text(), "one\n")
        self.assertFalse((self.wt / "later.txt").exists())
        # History is preserved: three commits, the restore on top.
        log = git_cli.log_all(self.wt)
        self.assertEqual(len(log), 3)
        self.assertEqual(log[0]["sha"], new)
        self.assertTrue(log[0]["message"].startswith("Restore to "))

    def test_restore_to_head_state_refuses(self):
        self._write("a.txt", "one\n")
        sha = self._commit("only")
        with self.assertRaises(git_cli.GitError):
            git_cli.restore_to(self.wt, sha, fake_user())


class PullConflictTests(GitCliTests):
    """A conflicted PULL must be as resolvable as a conflicted merge.

    Pull merges a remote branch instead of a local one; everything after that
    is the same operation. It used to raise MergeConflict with the paths and
    nothing else, so the resolver UI — which needs ours/theirs/merged per
    file — had nothing to render and the user was simply stuck.
    """

    def _remote_conflict(self):
        """A clone whose origin has moved on, conflicting with local work."""
        origin = Path(self.tmp.name) / "origin"
        git_cli.init(origin)
        (origin / "a.txt").write_text("base\n")
        git_cli.add_all(origin)
        git_cli.commit(origin, "base", fake_user())

        git_cli.run_git(["clone", str(origin), str(self.wt / ".." / "clone")],
                        cwd=Path(self.tmp.name))
        clone = Path(self.tmp.name) / "clone"

        # origin moves on
        (origin / "a.txt").write_text("remote change\n")
        git_cli.add_all(origin)
        git_cli.commit(origin, "remote change", fake_user())
        # …and so does the clone, incompatibly
        (clone / "a.txt").write_text("local change\n")
        git_cli.add_all(clone)
        git_cli.commit(clone, "local change", fake_user())
        return clone

    def test_a_conflicted_pull_carries_resolution_material(self):
        clone = self._remote_conflict()
        branch = git_cli.head_branch(clone)

        with self.assertRaises(git_cli.MergeConflict) as ctx:
            git_cli.pull(clone, branch, "", "", fake_user())

        self.assertEqual(ctx.exception.paths, ["a.txt"])
        detail = ctx.exception.details[0]
        self.assertEqual(detail["path"], "a.txt")
        self.assertTrue(detail["editable"])
        self.assertEqual(detail["ours"], "local change\n")
        self.assertEqual(detail["theirs"], "remote change\n")
        self.assertIn("<<<<<<<", detail["merged"])
        # …and the tree is restored, exactly as a local merge conflict leaves it
        self.assertEqual((clone / "a.txt").read_text(), "local change\n")


class MergeResolutionTests(GitCliTests):
    """Conflicted merges: rich detail on refusal, per-file settlement on re-run."""

    def _conflict(self):
        self._write("a.txt", "base\n")
        self._commit("base")
        git_cli.branch_create(self.wt, "feature")
        self._write("a.txt", "main change\n")
        self._commit("main change")
        git_cli.checkout(self.wt, "feature")
        self._write("a.txt", "feature change\n")
        self._commit("feature change")
        git_cli.checkout(self.wt, "main")

    def test_conflict_carries_resolution_material(self):
        self._conflict()
        with self.assertRaises(git_cli.MergeConflict) as ctx:
            git_cli.merge(self.wt, "feature", fake_user())
        detail = ctx.exception.details[0]
        self.assertEqual(detail["path"], "a.txt")
        self.assertTrue(detail["editable"])
        self.assertEqual(detail["ours"], "main change\n")
        self.assertEqual(detail["theirs"], "feature change\n")
        self.assertIn("<<<<<<<", detail["merged"])
        # and the abort still restored the tree
        self.assertEqual((self.wt / "a.txt").read_text(), "main change\n")

    def test_resolve_ours(self):
        self._conflict()
        sha = git_cli.merge(self.wt, "feature", fake_user(),
                            resolutions={"a.txt": "ours"})
        self.assertEqual(len(sha), 40)
        self.assertEqual((self.wt / "a.txt").read_text(), "main change\n")
        # A real merge commit: two parents.
        self.assertEqual(len(git_cli.log_all(self.wt)[0]["parents"]), 2)

    def test_resolve_theirs(self):
        self._conflict()
        git_cli.merge(self.wt, "feature", fake_user(),
                      resolutions={"a.txt": "theirs"})
        self.assertEqual((self.wt / "a.txt").read_text(), "feature change\n")

    def test_resolve_with_edited_content(self):
        self._conflict()
        git_cli.merge(self.wt, "feature", fake_user(),
                      resolutions={"a.txt": {"content": "both, reconciled\n"}})
        self.assertEqual((self.wt / "a.txt").read_text(), "both, reconciled\n")

    def test_missing_resolution_aborts_again(self):
        self._conflict()
        # b.txt conflicts too: add it differently on both sides.
        self._write("b.txt", "base b\n")
        self._commit("main adds b")
        git_cli.checkout(self.wt, "feature")
        self._write("b.txt", "feature b\n")
        self._commit("feature adds b")
        git_cli.checkout(self.wt, "main")
        with self.assertRaises(git_cli.MergeConflict) as ctx:
            git_cli.merge(self.wt, "feature", fake_user(),
                          resolutions={"a.txt": "ours"})
        self.assertIn("b.txt", ctx.exception.paths)
        self.assertEqual(git_cli.status(self.wt), [])  # clean, nothing stuck

    def test_invalid_resolution_aborts(self):
        self._conflict()
        with self.assertRaises(git_cli.GitError):
            git_cli.merge(self.wt, "feature", fake_user(),
                          resolutions={"a.txt": "flip-a-coin"})
        self.assertEqual(git_cli.status(self.wt), [])
