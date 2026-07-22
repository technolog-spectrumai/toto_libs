from django.test import SimpleTestCase

from toto.formica.colony.walker import edge_key, run_walks, walk


def _triangle():
    import networkx as nx

    graph = nx.Graph()
    graph.add_edge("a", "b", _ph=1.0, eta=1.0)
    graph.add_edge("b", "c", _ph=1.0, eta=1.0)
    graph.add_edge("a", "c", _ph=1.0, eta=1.0)
    return graph


class WalkerTests(SimpleTestCase):
    def test_edge_key_is_canonical(self):
        self.assertEqual(edge_key("b", "a"), ("a", "b"))
        self.assertEqual(edge_key("a", "b"), ("a", "b"))

    def test_deterministic_under_fixed_seed(self):
        graph = _triangle()
        kwargs = dict(n_ants=20, max_steps=6, alpha=1.0, beta=1.0,
                      epsilon=0.2, seed=42)
        first = run_walks(graph, **kwargs)
        second = run_walks(graph, **kwargs)
        self.assertEqual(first.edge_visits, second.edge_visits)
        self.assertEqual(first.node_visits, second.node_visits)
        self.assertEqual(first.covisits, second.covisits)

    def test_different_seeds_diverge(self):
        graph = _triangle()
        kwargs = dict(n_ants=20, max_steps=6, alpha=1.0, beta=1.0, epsilon=0.2)
        first = run_walks(graph, seed=1, **kwargs)
        second = run_walks(graph, seed=2, **kwargs)
        self.assertNotEqual(first.edge_visits, second.edge_visits)

    def test_max_steps_respected(self):
        import random

        graph = _triangle()
        nodes, edges = walk(
            graph, "a", max_steps=3, alpha=1, beta=1, epsilon=0,
            rng=random.Random(7),
        )
        self.assertLessEqual(len(edges), 3)
        self.assertEqual(len(nodes), len(edges) + 1)

    def test_walk_stops_at_dead_end(self):
        import networkx as nx
        import random

        graph = nx.Graph()
        graph.add_edge("a", "b", _ph=1.0, eta=1.0)  # b is a dead end (degree 1 back to a)
        graph.add_node("z")                          # isolated
        nodes, edges = walk(
            graph, "z", max_steps=5, alpha=1, beta=1, epsilon=0,
            rng=random.Random(7),
        )
        self.assertEqual(edges, [])
        self.assertEqual(nodes, ["z"])

    def test_pheromone_biases_choice(self):
        import networkx as nx

        # star: center → hot and cold; with high alpha the hot edge dominates
        graph = nx.Graph()
        graph.add_edge("center", "hot", _ph=100.0, eta=1.0)
        graph.add_edge("center", "cold", _ph=0.01, eta=1.0)
        result = run_walks(
            graph, n_ants=200, max_steps=1, alpha=2.0, beta=1.0,
            epsilon=0.0, seed=5, start_nodes=["center"],
        )
        hot = result.edge_visits.get(edge_key("center", "hot"), 0)
        cold = result.edge_visits.get(edge_key("center", "cold"), 0)
        self.assertGreater(hot, cold * 10)

    def test_full_exploration_ignores_trails(self):
        import networkx as nx

        graph = nx.Graph()
        graph.add_edge("center", "hot", _ph=100.0, eta=1.0)
        graph.add_edge("center", "cold", _ph=0.01, eta=1.0)
        result = run_walks(
            graph, n_ants=400, max_steps=1, alpha=2.0, beta=1.0,
            epsilon=1.0, seed=5, start_nodes=["center"],
        )
        hot = result.edge_visits.get(edge_key("center", "hot"), 0)
        cold = result.edge_visits.get(edge_key("center", "cold"), 0)
        self.assertLess(abs(hot - cold), 100)  # roughly uniform

    def test_covisits_count_pairs_once_per_walk(self):
        import networkx as nx

        graph = nx.Graph()
        graph.add_edge("a", "b", _ph=1.0, eta=1.0)
        result = run_walks(
            graph, n_ants=1, max_steps=5, alpha=1, beta=1,
            epsilon=0, seed=3, start_nodes=["a"],
        )
        # one walk bouncing a↔b: the pair is co-visited exactly once
        self.assertEqual(result.covisits.get(("a", "b")), 1)

    def test_empty_graph_and_zero_ants(self):
        import networkx as nx

        empty = run_walks(nx.Graph(), n_ants=5, max_steps=3, alpha=1, beta=1,
                          epsilon=0, seed=1)
        self.assertEqual(empty.ants_walked, 0)
        graph = _triangle()
        none = run_walks(graph, n_ants=0, max_steps=3, alpha=1, beta=1,
                         epsilon=0, seed=1)
        self.assertEqual(none.ants_walked, 0)
