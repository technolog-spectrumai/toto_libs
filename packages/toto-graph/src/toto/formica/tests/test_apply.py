from unittest.mock import patch

from django.test import TestCase, override_settings

from toto.bento import graph_service
from toto.formica.models import ColonyCycle, FormicaProposal
from toto.formica.services import apply as apply_svc

from .helpers import make_bento_templates, make_colony


def _node_payload(uid, slug="person", props=None):
    return {
        "uid": uid, "id": uid, "label": "Person", "category_slug": slug,
        "category_name": slug.title(), "display": uid,
        "properties": {"name": uid.title(), **(props or {})},
    }


def _op(op_id, action, params, approval="approved"):
    return {
        "op_id": op_id, "action": action, "params": params,
        "reason": "test", "evidence": {}, "validation": {},
        "approval": approval, "result": {},
    }


@override_settings(RAVIOLI_ENABLED=True)
class ValidateOpTests(TestCase):
    def setUp(self):
        make_bento_templates()

    def test_unknown_action(self):
        errors = apply_svc.validate_op(_op("o1", "explode", {}))
        self.assertTrue(any("Unknown action" in e for e in errors))

    def test_create_edge_requires_endpoints(self):
        errors = apply_svc.validate_op(_op("o1", "create_edge", {"edge_type_slug": "knows"}))
        self.assertTrue(errors)

    def test_create_edge_validates_endpoint_categories(self):
        def fake_get_node(uid):
            slug = "person" if uid.startswith("p") else "org"
            return _node_payload(uid, slug)

        with patch.object(graph_service, "get_node", side_effect=fake_get_node):
            ok = apply_svc.validate_op(_op("o1", "create_edge", {
                "edge_type_slug": "works-at", "from_uid": "p1", "to_uid": "o1",
            }))
            bad = apply_svc.validate_op(_op("o2", "create_edge", {
                "edge_type_slug": "works-at", "from_uid": "o1", "to_uid": "p1",
            }))
        self.assertEqual(ok, [])
        self.assertTrue(any("not an allowed" in e for e in bad))

    def test_create_edge_missing_node(self):
        with patch.object(graph_service, "get_node",
                          side_effect=graph_service.NotFound("Node not found.")):
            errors = apply_svc.validate_op(_op("o1", "create_edge", {
                "edge_type_slug": "knows", "from_uid": "x", "to_uid": "y",
            }))
        self.assertTrue(errors)

    def test_update_node_merges_declared_props_for_required_check(self):
        # The op only patches 'note' — validation must still pass because the
        # node's current declared 'name' is merged in (the update_node trap).
        with patch.object(graph_service, "get_node",
                          return_value=_node_payload("p1")):
            errors = apply_svc.validate_op(_op("o1", "update_node", {
                "uid": "p1", "properties": {"note": "hi"},
            }))
        self.assertEqual(errors, [])

    def test_declared_merge_never_leaks_markers(self):
        node = _node_payload("p1", props={"_ph_alarm": 3, "extra": {"x": 1}})
        from toto.bento.models import BentoCategory

        merged = apply_svc._declared_merge(
            node, BentoCategory.objects.get(slug="person"), {"note": "fix"}
        )
        self.assertEqual(set(merged), {"name", "note"})

    def test_resync_slice_requires_existing_sql_row(self):
        errors = apply_svc.validate_op(_op("o1", "update_node", {
            "resync_slice": {"label": "NoSuchLabel", "uuid": "u1"},
        }))
        self.assertTrue(any("No SQL model" in e for e in errors))

    def test_delete_nodes_requires_uids(self):
        self.assertTrue(apply_svc.validate_op(_op("o1", "delete_nodes", {"uids": []})))
        self.assertEqual(
            apply_svc.validate_op(_op("o1", "delete_nodes", {"uids": ["a"]})), []
        )


@override_settings(RAVIOLI_ENABLED=True)
class ApproveAllValidTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.colony = make_colony()
        self.cycle = ColonyCycle.objects.create(colony=self.colony, number=1, rng_seed=1)

    def test_invalid_ops_never_approved(self):
        proposal = FormicaProposal.objects.create(
            colony=self.colony, cycle=self.cycle,
            ops={"ops": [
                _op("o1", "delete_nodes", {"uids": ["a"]}, approval="pending"),
                _op("o2", "delete_nodes", {"uids": []}, approval="pending"),
            ]},
        )
        apply_svc.approve_all_valid(proposal)
        proposal.refresh_from_db()
        ops = {o["op_id"]: o for o in proposal.op_list}
        self.assertEqual(ops["o1"]["approval"], "approved")
        self.assertEqual(ops["o2"]["approval"], "pending")
        self.assertEqual(proposal.summary["errors"], 1)


@override_settings(RAVIOLI_ENABLED=True)
class ApplyOpsTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.colony = make_colony()
        self.cycle = ColonyCycle.objects.create(colony=self.colony, number=1, rng_seed=1)

    def _proposal(self, ops):
        return FormicaProposal.objects.create(
            colony=self.colony, cycle=self.cycle, ops={"ops": ops},
        )

    def test_apply_all_three_actions(self):
        def fake_get_node(uid):
            slug = "person" if uid.startswith("p") else "org"
            return _node_payload(uid, slug)

        ops = [
            _op("o1", "create_edge", {"edge_type_slug": "works-at",
                                      "from_uid": "p1", "to_uid": "o1",
                                      "properties": {}}),
            _op("o2", "update_node", {"uid": "p1", "properties": {"note": "fixed"}}),
            _op("o3", "delete_nodes", {"uids": ["dead-1", "dead-2"]}),
        ]
        proposal = self._proposal(ops)
        with patch.object(graph_service, "get_node", side_effect=fake_get_node), \
             patch.object(graph_service, "edge_exists", return_value=False), \
             patch.object(graph_service, "create_edge", return_value={"id": "eid-1"}) as ce, \
             patch.object(graph_service, "update_node", return_value={}) as un, \
             patch.object(graph_service, "delete_nodes", return_value=2) as dn:
            _result, errors = apply_svc.run(proposal.pk, user_id=None)
        self.assertEqual(errors, [])
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, FormicaProposal.STATUS_APPLIED)
        self.assertEqual(len(proposal.apply_result["done"]), 3)
        ce.assert_called_once_with("works-at", "p1", "o1", {})
        # update merged the current declared name in (required-prop trap)
        un.assert_called_once()
        sent_props = un.call_args.args[1]
        self.assertEqual(sent_props["name"], "P1")
        self.assertEqual(sent_props["note"], "fixed")
        dn.assert_called_once_with(["dead-1", "dead-2"])
        self.assertIsNotNone(proposal.applied_at)

    def test_existing_edge_skipped_not_duplicated(self):
        def fake_get_node(uid):
            return _node_payload(uid, "person")

        ops = [_op("o1", "create_edge", {"edge_type_slug": "knows",
                                         "from_uid": "p1", "to_uid": "p2"})]
        proposal = self._proposal(ops)
        with patch.object(graph_service, "get_node", side_effect=fake_get_node), \
             patch.object(graph_service, "edge_exists", return_value=True), \
             patch.object(graph_service, "create_edge") as ce:
            _result, errors = apply_svc.run(proposal.pk)
        ce.assert_not_called()
        self.assertEqual(errors, [])
        proposal.refresh_from_db()
        self.assertIn("skipped", proposal.apply_result["done"]["o1"]["detail"])

    def test_pending_and_rejected_ops_are_not_applied(self):
        ops = [
            _op("o1", "delete_nodes", {"uids": ["a"]}, approval="pending"),
            _op("o2", "delete_nodes", {"uids": ["b"]}, approval="rejected"),
        ]
        proposal = self._proposal(ops)
        with patch.object(graph_service, "delete_nodes") as dn:
            apply_svc.run(proposal.pk)
        dn.assert_not_called()

    def test_partial_failure_is_resumable(self):
        ops = [
            _op("o1", "delete_nodes", {"uids": ["a"]}),
            _op("o2", "delete_nodes", {"uids": ["b"]}),
        ]
        proposal = self._proposal(ops)
        calls = {"n": 0}

        def flaky(uids):
            calls["n"] += 1
            if uids == ["b"] and calls["n"] < 3:
                raise RuntimeError("connection lost")
            return 1

        with patch.object(graph_service, "delete_nodes", side_effect=flaky):
            _result, errors = apply_svc.run(proposal.pk)
            self.assertTrue(errors)
            proposal.refresh_from_db()
            self.assertEqual(proposal.status, FormicaProposal.STATUS_FAILED)
            self.assertIn("o1", proposal.apply_result["done"])

            # retry: o1 must NOT be re-applied, o2 succeeds now
            _result, errors = apply_svc.run(proposal.pk)
        self.assertEqual(errors, [])
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, FormicaProposal.STATUS_APPLIED)
        # delete called once for a (first run) and twice for b (fail + retry)
        self.assertEqual(calls["n"], 3)

    def test_applied_proposal_is_a_noop(self):
        proposal = self._proposal([_op("o1", "delete_nodes", {"uids": ["a"]})])
        proposal.status = FormicaProposal.STATUS_APPLIED
        proposal.save()
        with patch.object(graph_service, "delete_nodes") as dn:
            _result, errors = apply_svc.run(proposal.pk)
        dn.assert_not_called()
        self.assertEqual(errors, [])
