"""Lane layout invariants on a real branched+merged repo."""

from toto.repo import git_cli, history, services
from toto.vault.models import VaultFile

from .base import RepoTestCase


class HistoryGraphTests(RepoTestCase):
    def _edit_commit(self, content, message, repo):
        with VaultFile.objects.get(pk=self.f_notes.pk).file.open("wb") as fh:
            fh.write(content)
        return services.commit(repo, self.user, message)

    def test_linear_history(self):
        repo = self.make_repo()
        self._edit_commit(b"2\n", "second", repo)
        graph = history.history_graph(repo)
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual([n["lane"] for n in graph["nodes"]], [0, 0])
        self.assertEqual([n["y"] for n in graph["nodes"]], [0, -1])
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["head"], "main")
        self.assertTrue(graph["nodes"][0]["is_head"])
        self.assertIn("main", graph["nodes"][0]["refs"])

    def test_branch_and_merge_lanes(self):
        repo = self.make_repo()
        # build: main: A --------- D(merge)
        #                 \ feature: B,C /   plus a main-side commit
        services.branch_create(repo, "feature", checkout=True, user=self.user)
        self._edit_commit(b"f1\n", "feature-1", repo)
        # add a second file on feature so the merge is clean
        new = repo.worktree  # commit via vault flow keeps sync invariants
        services.checkout(repo, "main", self.user)
        self._file("main-only.txt", b"m\n", self.root)
        services.commit(repo, self.user, "main-side")
        result = services.merge(repo, "feature", self.user)

        graph = history.history_graph(repo)
        nodes = {n["id"]: n for n in graph["nodes"]}
        # no two nodes share a cell
        cells = [(n["x"], n["y"]) for n in graph["nodes"]]
        self.assertEqual(len(cells), len(set(cells)))
        # the merge commit has two parents → two outgoing edges
        merge_sha = result["sha"]
        out_edges = [e for e in graph["edges"] if e["source"] == merge_sha]
        self.assertEqual(len(out_edges), 2)
        # at least two lanes were used
        self.assertGreaterEqual(max(n["lane"] for n in graph["nodes"]), 1)
        # every edge endpoint exists
        for e in graph["edges"]:
            self.assertIn(e["source"], nodes)
            self.assertIn(e["target"], nodes)

    def test_first_parent_lane_continuity(self):
        repo = self.make_repo()
        shas = [self._edit_commit(f"{i}\n".encode(), f"c{i}", repo) for i in range(3)]
        graph = history.history_graph(repo)
        lanes = {n["id"]: n["lane"] for n in graph["nodes"]}
        # a linear chain stays in lane 0 the whole way
        self.assertTrue(all(lane == 0 for lane in lanes.values()))

    def test_empty_repo_graph(self):
        from toto.repo.models import GitRepo
        from toto.repo import sync
        repo = GitRepo.objects.create(directory=self.sub, owner=self.user)
        git_cli.init(repo.worktree)
        graph = history.history_graph(repo)
        self.assertEqual(graph["nodes"], [])
        self.assertEqual(graph["edges"], [])
