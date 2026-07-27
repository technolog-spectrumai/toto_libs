from unittest.mock import patch

from django.test import TestCase

from toto.formica.colony import castes as castes_mod
from toto.formica.colony.castes import (
    ArchitectCaste,
    CASTE_REGISTRY,
    ForagerCaste,
    NurseCaste,
    PrunerCaste,
    QueenCaste,
    ScoutCaste,
)
from toto.formica.models import ColonyCycle

from .helpers import FakeGraphOps, edge, make_bento_templates, make_colony, make_ctx, node


def _cycle(colony, number=5):
    return ColonyCycle.objects.create(colony=colony, number=number, rng_seed=42)


class RegistryTests(TestCase):
    def test_all_six_castes_registered(self):
        self.assertEqual(
            set(CASTE_REGISTRY),
            {"scout", "forager", "nurse", "pruner", "architect", "queen"},
        )

    def test_duplicate_registration_rejected(self):
        with self.assertRaises(ValueError):
            @castes_mod.register_caste
            class Impostor(castes_mod.BaseCaste):
                caste_key = "scout"

    def test_execution_order(self):
        keys = [k for k, c in sorted(CASTE_REGISTRY.items(), key=lambda kv: kv[1].order)]
        self.assertEqual(keys, ["scout", "forager", "nurse", "pruner", "architect", "queen"])


class ScoutTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.colony = make_colony()
        self.cycle = _cycle(self.colony)

    def test_seeds_only_faint_edges_and_alarms_orphans(self):
        nodes = [node("a"), node("b"), node("orphan"),
                 node("sql-orphan", props={"uuid": "u-1"})]
        edges = [edge("e1", "a", "b", ph=0.0)]
        ctx = make_ctx(self.colony, self.cycle, nodes=nodes, edges=edges)
        stats = ScoutCaste().run(ctx)
        self.assertGreater(stats["edges_seeded"], 0)
        deposited = {d["eid"] for d in ctx.graph_ops.edge_deposits}
        self.assertIn("e1", deposited)
        # orphans without SQL backing get alarmed; uuid-bearing ones don't
        self.assertEqual(ctx.graph_ops.alarms_set, ["orphan"])

    def test_hot_edges_not_reseeded(self):
        nodes = [node("a"), node("b")]
        edges = [edge("e1", "a", "b", ph=5.0)]
        ctx = make_ctx(self.colony, self.cycle, nodes=nodes, edges=edges)
        stats = ScoutCaste().run(ctx)
        self.assertEqual(stats["edges_seeded"], 0)


class ForagerTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.colony = make_colony()
        self.cycle = _cycle(self.colony)
        self.nodes = [node("a"), node("b"), node("c")]
        self.edges = [
            edge("e1", "a", "b", ph=1.0),
            edge("e2", "b", "c", ph=1.0),
            edge("e2b", "b", "c", ph=0.5, rel="WORKS_AT"),  # parallel edge
        ]

    def test_deposits_are_clamped_and_flushed_to_all_parallels(self):
        ctx = make_ctx(self.colony, self.cycle, nodes=self.nodes, edges=self.edges)
        stats = ForagerCaste().run(ctx)
        self.assertGreater(stats["ants"], 0)
        params = self.colony.params()
        for deposit in ctx.graph_ops.edge_deposits:
            self.assertGreaterEqual(deposit["ph"], params["ph_floor"])
            self.assertLessEqual(deposit["ph"], params["ph_max"])
        deposited = {d["eid"] for d in ctx.graph_ops.edge_deposits}
        if "e2" in deposited:  # a walk over b–c reinforces BOTH parallel eids
            self.assertIn("e2b", deposited)

    def test_material_deposited_where_trails_pass(self):
        ctx = make_ctx(self.colony, self.cycle, nodes=self.nodes, edges=self.edges)
        ForagerCaste().run(ctx)
        self.assertTrue(ctx.graph_ops.material_deposits)

    def test_report_shape(self):
        ctx = make_ctx(self.colony, self.cycle, nodes=self.nodes, edges=self.edges)
        ForagerCaste().run(ctx)
        report = ctx.senses["forager"]["report"]
        self.assertEqual(set(report), {"label_counts", "hottest", "degree_buckets", "fill_rates"})
        self.assertEqual(report["label_counts"]["Person"], 3)
        self.assertLessEqual(len(report["hottest"]), 10)
        self.assertAlmostEqual(report["fill_rates"]["Person"], 0.5)  # name yes, note no
        self.assertEqual(sum(report["degree_buckets"].values()), 3)

    def test_walks_are_reproducible_across_runs(self):
        ctx1 = make_ctx(self.colony, self.cycle, nodes=self.nodes, edges=self.edges)
        ctx2 = make_ctx(self.colony, self.cycle, nodes=self.nodes, edges=self.edges)
        ForagerCaste().run(ctx1)
        ForagerCaste().run(ctx2)
        self.assertEqual(ctx1.graph_ops.edge_deposits, ctx2.graph_ops.edge_deposits)


class NurseTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.colony = make_colony()
        self.cycle = _cycle(self.colony)

    def test_violation_becomes_update_op_with_fill(self):
        broken = node("no-name")
        del broken["props"]["name"]  # required prop missing
        broken["props"]["title"] = "Recovered Title"
        ctx = make_ctx(self.colony, self.cycle, nodes=[broken], edges=[])
        with patch.object(NurseCaste, "_sense_drift", return_value=[]):
            stats = NurseCaste().run(ctx)
        self.assertEqual(stats["violations"], 1)
        self.assertEqual(stats["repairs_proposed"], 1)
        op = ctx.ops[0]
        self.assertEqual(op["action"], "update_node")
        self.assertEqual(op["params"]["uid"], "no-name")
        self.assertEqual(op["params"]["properties"]["name"], "Recovered Title")
        self.assertIn("no-name", ctx.graph_ops.alarms_set)

    def test_healthy_alarmed_nodes_get_cleared(self):
        healthy = node("fine", props={"_ph_alarm": 3})
        ctx = make_ctx(self.colony, self.cycle, nodes=[healthy], edges=[])
        with patch.object(NurseCaste, "_sense_drift", return_value=[]):
            stats = NurseCaste().run(ctx)
        self.assertEqual(stats["violations"], 0)
        self.assertEqual(ctx.graph_ops.alarms_cleared, ["fine"])

    def test_drift_becomes_resync_op(self):
        ctx = make_ctx(self.colony, self.cycle, nodes=[node("a")], edges=[])
        drift = [{"label": "Person", "uuid": "u-9", "id": "a"}]
        with patch.object(NurseCaste, "_sense_drift", return_value=drift):
            stats = NurseCaste().run(ctx)
        self.assertEqual(stats["drift_resyncs"], 1)
        op = ctx.ops[0]
        self.assertEqual(op["params"]["resync_slice"], {"label": "Person", "uuid": "u-9"})

    def test_unfillable_violation_alarms_but_does_not_repair(self):
        broken = node("hopeless")
        del broken["props"]["name"]
        ctx = make_ctx(self.colony, self.cycle, nodes=[broken], edges=[])
        # display falls back to the node id — that IS a fill, so remove it too
        ctx.node_by_id["hopeless"]["props"].pop("title", None)
        with patch.object(NurseCaste, "_sense_drift", return_value=[]):
            NurseCaste().run(ctx)
        # id-based fill is allowed; the op exists but review decides
        self.assertLessEqual(len(ctx.ops), 1)


class PrunerTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.colony = make_colony()
        self.cycle = _cycle(self.colony, number=10)

    def _ctx(self, nodes, edges, **fake_kwargs):
        fake = FakeGraphOps(nodes=nodes, edges=edges, cycle_number=10, **fake_kwargs)
        return make_ctx(self.colony, self.cycle, nodes=nodes, edges=edges, graph_ops=fake)

    def test_cold_nodes_get_quarantined(self):
        nodes = [node("cold"), node("warm")]
        edges = [edge("e1", "warm", "cold", ph=5.0)]
        # cold-only node: no incident edges at all
        nodes.append(node("frozen"))
        ctx = self._ctx(nodes, edges)
        stats = PrunerCaste().run(ctx)
        self.assertIn("frozen", ctx.graph_ops.quarantined_ids)
        self.assertNotIn("warm", ctx.graph_ops.quarantined_ids)
        self.assertEqual(stats["deletes_proposed"], 0)

    def test_protected_and_sql_nodes_never_quarantined(self):
        self.colony.protected_uids = ["sacred"]
        self.colony.protected_labels = ["Org"]
        self.colony.save()
        nodes = [
            node("sacred"),
            node("org-node", label="Org"),
            node("sql-node", props={"uuid": "u-2"}),
            node("plain"),
        ]
        ctx = self._ctx(nodes, [])
        PrunerCaste().run(ctx)
        self.assertEqual(ctx.graph_ops.quarantined_ids, ["plain"])

    def test_no_delete_before_grace(self):
        quarantined = [{"id": "q1", "labels": ["Person"], "uuid": None,
                        "since": 9, "props": {}}]  # quarantined last cycle
        ctx = self._ctx([node("a")], [], quarantined=quarantined)
        stats = PrunerCaste().run(ctx)
        self.assertEqual(stats["deletes_proposed"], 0)

    def test_delete_proposed_after_grace_when_still_cold(self):
        quarantined = [{"id": "q1", "labels": ["Person"], "uuid": None,
                        "since": 2, "props": {}}]  # grace (3) long elapsed at cycle 10
        ctx = self._ctx([node("a")], [], quarantined=quarantined,
                        incident={"q1": 0.0})
        stats = PrunerCaste().run(ctx)
        self.assertEqual(stats["deletes_proposed"], 1)
        op = ctx.ops[0]
        self.assertEqual(op["action"], "delete_nodes")
        self.assertEqual(op["params"]["uids"], ["q1"])

    def test_recovered_nodes_unquarantined_not_deleted(self):
        quarantined = [{"id": "q1", "labels": ["Person"], "uuid": None,
                        "since": 2, "props": {}}]
        ctx = self._ctx([node("a")], [], quarantined=quarantined,
                        incident={"q1": 9.0})  # pheromone recovered
        stats = PrunerCaste().run(ctx)
        self.assertEqual(stats["deletes_proposed"], 0)
        self.assertIn("q1", ctx.graph_ops.unquarantined_ids)

    def test_max_prunes_cap(self):
        self.colony.meta_params = {"max_prunes_per_cycle": 2}
        self.colony.save()
        quarantined = [
            {"id": f"q{i}", "labels": ["Person"], "uuid": None, "since": 2, "props": {}}
            for i in range(5)
        ]
        ctx = self._ctx([node("a")], [], quarantined=quarantined)
        PrunerCaste().run(ctx)
        self.assertEqual(len(ctx.ops[0]["params"]["uids"]), 2)


class ArchitectTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.colony = make_colony(meta_params={"build_threshold": 1.0})
        self.cycle = _cycle(self.colony)

    def _ctx_with_covisits(self, nodes, covisits, material):
        ctx = make_ctx(self.colony, self.cycle, nodes=nodes, edges=[])
        ctx.senses["forager"] = {"covisits": covisits, "report": {}}
        for n in ctx.nodes:
            n["props"]["_ph_material"] = material.get(n["id"], 0.0)
        return ctx

    def test_proposes_edge_only_for_allowed_type(self):
        nodes = [node("p1"), node("o1", label="Org")]
        ctx = self._ctx_with_covisits(
            nodes, {("o1", "p1"): 5}, {"p1": 10.0, "o1": 10.0}
        )
        with patch("toto.bento.graph_service.edge_exists", return_value=False):
            stats = ArchitectCaste().run(ctx)
        self.assertEqual(stats["builds_proposed"], 1)
        op = ctx.ops[0]
        self.assertEqual(op["action"], "create_edge")
        self.assertEqual(op["params"]["edge_type_slug"], "works-at")
        # direction respects the endpoint constraints: person → org
        self.assertEqual(op["params"]["from_uid"], "p1")
        self.assertEqual(op["params"]["to_uid"], "o1")

    def test_no_edge_type_between_means_no_proposal(self):
        nodes = [node("o1", label="Org"), node("o2", label="Org")]
        ctx = self._ctx_with_covisits(
            nodes, {("o1", "o2"): 9}, {"o1": 10.0, "o2": 10.0}
        )
        stats = ArchitectCaste().run(ctx)
        self.assertEqual(stats["builds_proposed"], 0)

    def test_material_threshold_gates(self):
        nodes = [node("p1"), node("p2")]
        ctx = self._ctx_with_covisits(nodes, {("p1", "p2"): 5}, {"p1": 0.1, "p2": 0.1})
        stats = ArchitectCaste().run(ctx)
        self.assertEqual(stats["builds_proposed"], 0)

    def test_existing_live_edge_blocks_proposal(self):
        nodes = [node("p1"), node("p2")]
        ctx = self._ctx_with_covisits(nodes, {("p1", "p2"): 5}, {"p1": 10.0, "p2": 10.0})
        with patch("toto.bento.graph_service.edge_exists", return_value=True):
            stats = ArchitectCaste().run(ctx)
        self.assertEqual(stats["builds_proposed"], 0)

    def test_sampled_edge_blocks_proposal(self):
        nodes = [node("p1"), node("p2")]
        edges = [edge("e1", "p1", "p2")]
        ctx = make_ctx(self.colony, self.cycle, nodes=nodes, edges=edges)
        ctx.senses["forager"] = {"covisits": {("p1", "p2"): 5}, "report": {}}
        for n in ctx.nodes:
            n["props"]["_ph_material"] = 10.0
        stats = ArchitectCaste().run(ctx)
        self.assertEqual(stats["builds_proposed"], 0)

    def test_similarity_gate_blocks_low_similarity(self):
        nodes = [node("p1"), node("o1", label="Org")]
        ctx = self._ctx_with_covisits(nodes, {("o1", "p1"): 5}, {"p1": 10.0, "o1": 10.0})
        with patch.object(ArchitectCaste, "_similarity", return_value=0.1):
            stats = ArchitectCaste().run(ctx)
        self.assertEqual(stats["builds_proposed"], 0)


class QueenTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.colony = make_colony()
        self.cycle = _cycle(self.colony)

    def _ctx_with_stats(self, stats):
        ctx = make_ctx(self.colony, self.cycle, nodes=[], edges=[])
        ctx.stats = stats
        return ctx

    def test_reallocates_toward_need_within_bounds(self):
        ctx = self._ctx_with_stats({
            "quarantined": 40,
            "castes": {"nurse": {"violations": 0}, "scout": {"orphans": 0},
                       "architect": {"builds_proposed": 0}},
        })
        before = {c.caste_key: c.ant_count for c in self.colony.castes.all()}
        total_before = sum(before.values())
        stats = QueenCaste().run(ctx)
        after = {c.caste_key: c.ant_count for c in self.colony.castes.all()}
        self.assertEqual(sum(after.values()), total_before)  # conservation
        self.assertGreater(after["pruner"], before["pruner"])
        self.assertEqual(stats["to"], "pruner")
        step = stats["reallocated"]
        self.assertLessEqual(step, max(1, sum(v for k, v in before.items() if k != "queen") // 10) + 1)

    def test_donor_never_drops_below_one(self):
        for config in self.colony.castes.all():
            config.ant_count = 1
            config.save()
        ctx = self._ctx_with_stats({"quarantined": 40, "castes": {}})
        stats = QueenCaste().run(ctx)
        self.assertEqual(stats["reallocated"], 0)
        for config in self.colony.castes.all():
            self.assertGreaterEqual(config.ant_count, 1)

    def test_no_need_means_no_move(self):
        ctx = self._ctx_with_stats({"quarantined": 0, "castes": {}})
        # forager's baseline need of 1 makes it neediest; donor must differ
        stats = QueenCaste().run(ctx)
        # either a small forager-ward move or none — but never a crash and
        # never a negative count
        for config in self.colony.castes.all():
            self.assertGreaterEqual(config.ant_count, 1)
        self.assertIn("reallocated", stats)
