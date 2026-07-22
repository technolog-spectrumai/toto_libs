from unittest.mock import patch

from django.test import TestCase, override_settings

from toto.formica.colony import cycle as cycle_mod
from toto.formica.models import ColonyCycle, ForagerReport, FormicaProposal

from .helpers import FakeGraphOps, edge, make_bento_templates, make_colony, node


def _fake_ops_factory(nodes, edges, **kwargs):
    holder = {}

    def factory(*, write_budget, cycle_number):
        holder["ops"] = FakeGraphOps(
            nodes=nodes, edges=edges, write_budget=write_budget,
            cycle_number=cycle_number, **kwargs,
        )
        return holder["ops"]

    return factory, holder


@override_settings(RAVIOLI_ENABLED=True)
class ExecuteCycleTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.colony = make_colony()
        self.nodes = [node("a"), node("b"), node("c")]
        self.edges = [edge("e1", "a", "b", ph=1.0), edge("e2", "b", "c", ph=1.0)]

    def _run(self, cycle, nodes=None, edges=None, alive=True, **fake_kwargs):
        factory, holder = _fake_ops_factory(
            self.nodes if nodes is None else nodes,
            self.edges if edges is None else edges,
            **fake_kwargs,
        )
        with patch.object(cycle_mod, "GraphOps", side_effect=factory), \
             patch("toto.ravioli.connection.is_alive", return_value=alive), \
             patch("toto.bento.graph_service.edge_exists", return_value=False):
            cycle_mod.execute_cycle(cycle.pk)
        cycle.refresh_from_db()
        return cycle, holder.get("ops")

    def _cycle(self, **kwargs):
        return ColonyCycle.objects.create(
            colony=self.colony, number=1, rng_seed=7, **kwargs
        )

    def test_full_cycle_completes_with_stats_and_report(self):
        cycle, fake = self._run(self._cycle())
        self.assertEqual(cycle.status, ColonyCycle.STATUS_COMPLETED)
        self.assertEqual(cycle.stats["nodes_sampled"], 3)
        self.assertEqual(cycle.stats["edges_sampled"], 2)
        self.assertIn("castes", cycle.stats)
        self.assertIn("forager", cycle.stats["castes"])
        self.assertTrue(fake.evaporations)          # evaporate ran before castes
        self.assertTrue(ForagerReport.objects.filter(cycle=cycle).exists())
        self.assertIsNotNone(cycle.started_at)
        self.assertIsNotNone(cycle.finished_at)

    def test_graph_down_aborts_cleanly(self):
        cycle, _fake = self._run(self._cycle(), alive=False)
        self.assertEqual(cycle.status, ColonyCycle.STATUS_ABORTED)
        self.assertIn("unreachable", cycle.error)

    def test_paused_colony_aborts(self):
        self.colony.paused = True
        self.colony.save()
        cycle, _fake = self._run(self._cycle())
        self.assertEqual(cycle.status, ColonyCycle.STATUS_ABORTED)
        self.assertIn("paused", cycle.error.lower())

    def test_non_pending_cycle_is_skipped(self):
        cycle = self._cycle(status=ColonyCycle.STATUS_COMPLETED)
        with patch.object(cycle_mod, "GraphOps") as graph_ops:
            cycle_mod.execute_cycle(cycle.pk)
        graph_ops.assert_not_called()

    def test_exception_marks_cycle_failed(self):
        cycle = self._cycle()
        with patch.object(cycle_mod, "GraphOps", side_effect=RuntimeError("boom")), \
             patch("toto.ravioli.connection.is_alive", return_value=True):
            cycle_mod.execute_cycle(cycle.pk)
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, ColonyCycle.STATUS_FAILED)
        self.assertIn("boom", cycle.error)

    def test_write_budget_exhaustion_completes_with_flag(self):
        self.colony.meta_params = {"cycle_write_budget": 100}
        self.colony.save()
        # enough edges that evaporate + deposits blow the tiny budget
        many_nodes = [node(f"n{i}") for i in range(60)]
        many_edges = [
            edge(f"e{i}", f"n{i}", f"n{i + 1}", ph=1.0) for i in range(59)
        ]
        cycle, _fake = self._run(self._cycle(), nodes=many_nodes, edges=many_edges)
        self.assertEqual(cycle.status, ColonyCycle.STATUS_COMPLETED)
        self.assertTrue(cycle.stats.get("budget_exhausted"))

    def test_proposal_created_when_castes_emit_ops(self):
        # a quarantined node past grace guarantees a delete op
        quarantined = [{"id": "dead", "labels": ["Person"], "uuid": None,
                        "since": 1, "props": {}}]
        cycle = ColonyCycle.objects.create(colony=self.colony, number=9, rng_seed=7)
        with patch("toto.formica.services.apply.revalidate",
                   side_effect=lambda ops: ops):
            cycle, _fake = self._run(cycle, quarantined=quarantined)
        self.assertEqual(cycle.status, ColonyCycle.STATUS_COMPLETED)
        proposal = FormicaProposal.objects.get(cycle=cycle)
        self.assertEqual(proposal.status, FormicaProposal.STATUS_READY)
        actions = [op["action"] for op in proposal.op_list]
        self.assertIn("delete_nodes", actions)
        self.assertEqual(cycle.stats["proposal_id"], proposal.pk)

    def test_trusted_colony_auto_applies(self):
        self.colony.trusted = True
        self.colony.save()
        quarantined = [{"id": "dead", "labels": ["Person"], "uuid": None,
                        "since": 1, "props": {}}]
        cycle = ColonyCycle.objects.create(colony=self.colony, number=9, rng_seed=7)
        with patch("toto.formica.services.apply.revalidate", side_effect=lambda ops: ops), \
             patch("toto.formica.services.apply.run") as apply_run, \
             patch("toto.celery_utils.celery_available", return_value=False):
            cycle, _fake = self._run(cycle, quarantined=quarantined)
        apply_run.assert_called_once()

    def test_new_ready_proposal_expires_older_ones(self):
        old_cycle = ColonyCycle.objects.create(colony=self.colony, number=8, rng_seed=1)
        stale = FormicaProposal.objects.create(
            colony=self.colony, cycle=old_cycle,
            status=FormicaProposal.STATUS_READY, ops={"ops": []},
        )
        quarantined = [{"id": "dead", "labels": ["Person"], "uuid": None,
                        "since": 1, "props": {}}]
        cycle = ColonyCycle.objects.create(colony=self.colony, number=9, rng_seed=7)
        with patch("toto.formica.services.apply.revalidate", side_effect=lambda ops: ops):
            self._run(cycle, quarantined=quarantined)
        stale.refresh_from_db()
        self.assertEqual(stale.status, FormicaProposal.STATUS_EXPIRED)


class WalkGraphBuildTests(TestCase):
    def test_parallel_edges_collapse_and_keep_all_eids(self):
        nodes = [node("a"), node("b")]
        edges = [
            edge("e1", "a", "b", ph=1.0),
            edge("e2", "a", "b", ph=3.0, rel="WORKS_AT"),
        ]
        graph = cycle_mod._build_walk_graph(nodes, edges)
        data = graph["a"]["b"]
        self.assertEqual(sorted(data["eids"]), ["e1", "e2"])
        self.assertEqual(data["_ph"], 3.0)

    def test_self_loops_and_unknown_endpoints_skipped(self):
        nodes = [node("a")]
        edges = [edge("e1", "a", "a"), edge("e2", "a", "ghost")]
        graph = cycle_mod._build_walk_graph(nodes, edges)
        self.assertEqual(graph.number_of_edges(), 0)
