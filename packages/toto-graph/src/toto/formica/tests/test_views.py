import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.formica.models import ColonyCycle, FormicaProposal

from .helpers import make_bento_templates, make_colony


def _op(op_id, action="delete_nodes", params=None, approval="pending",
        validation=None):
    return {
        "op_id": op_id, "action": action,
        "params": params or {"uids": ["x"]},
        "reason": "test", "evidence": {},
        "validation": validation or {"status": "ok", "errors": []},
        "approval": approval, "result": {},
    }


class PermissionTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="T", author="T", publication_year=2024, active=True)
        make_bento_templates()
        self.colony = make_colony()
        cycle = ColonyCycle.objects.create(colony=self.colony, number=1)
        self.proposal = FormicaProposal.objects.create(
            colony=self.colony, cycle=cycle, ops={"ops": [_op("o1")]},
        )

    def _routes(self):
        return [
            ("get", reverse("formica:overview")),
            ("get", reverse("formica:params")),
            ("get", reverse("formica:cycles")),
            ("get", reverse("formica:map")),
            ("get", reverse("formica:proposals")),
            ("get", reverse("formica:proposal_detail", args=[self.proposal.pk])),
            ("post", reverse("formica:api_run_now")),
            ("post", reverse("formica:api_pause")),
            ("post", reverse("formica:api_trusted")),
            ("get", reverse("formica:api_cycle_chart")),
            ("get", reverse("formica:api_pheromone_map")),
            ("post", reverse("formica:api_proposal_op", args=[self.proposal.pk, "o1"])),
            ("post", reverse("formica:api_proposal_apply", args=[self.proposal.pk])),
        ]

    def test_anonymous_redirected_everywhere(self):
        for method, url in self._routes():
            res = getattr(self.client, method)(url)
            self.assertEqual(res.status_code, 302, url)

    def test_non_superuser_redirected_everywhere(self):
        User.objects.create_user("bob", password="x")
        self.client.login(username="bob", password="x")
        for method, url in self._routes():
            res = getattr(self.client, method)(url)
            self.assertEqual(res.status_code, 302, url)


class SuperuserPagesTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="T", author="T", publication_year=2024, active=True)
        make_bento_templates()
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")

    def test_overview_without_colony_prompts_ingress(self):
        res = self.client.get(reverse("formica:overview"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "ingress_formica")

    def test_overview_with_colony_shows_controls(self):
        make_colony(name="Colonia Prima")
        with patch("toto.celery_utils.celery_available", return_value=True):
            res = self.client.get(reverse("formica:overview"))
        self.assertContains(res, "Colonia Prima")
        self.assertContains(res, "Run cycle now")

    def test_params_page_renders_every_spec(self):
        from toto.formica.params import PARAM_SPECS

        make_colony()
        res = self.client.get(reverse("formica:params"))
        for name in PARAM_SPECS:
            self.assertContains(res, f"param_{name}")

    def test_params_save_and_reset(self):
        colony = make_colony()
        payload = {"interval_minutes": "30", "param_evaporation_rate": "0.25"}
        for row in colony.castes.all():
            payload[f"caste_{row.caste_key}_enabled"] = "on"
            payload[f"caste_{row.caste_key}_ants"] = str(row.ant_count)
        res = self.client.post(reverse("formica:params"), payload)
        self.assertEqual(res.status_code, 302)
        colony.refresh_from_db()
        self.assertEqual(colony.meta_params["evaporation_rate"], 0.25)
        self.assertEqual(colony.interval_minutes, 30)

        res = self.client.post(reverse("formica:params"), {"reset": "1"})
        colony.refresh_from_db()
        self.assertEqual(colony.meta_params, {})

    def test_params_save_rejects_out_of_range(self):
        colony = make_colony(meta_params={"evaporation_rate": 0.2})
        res = self.client.post(
            reverse("formica:params"),
            {"interval_minutes": "60", "param_evaporation_rate": "0.9"},
            follow=True,
        )
        colony.refresh_from_db()
        self.assertEqual(colony.meta_params["evaporation_rate"], 0.2)  # unchanged
        self.assertContains(res, "between")

    def test_cycles_page_lists_history(self):
        colony = make_colony()
        ColonyCycle.objects.create(
            colony=colony, number=1, status=ColonyCycle.STATUS_COMPLETED,
            stats={"nodes_sampled": 5, "edges_sampled": 3, "writes_used": 11},
        )
        res = self.client.get(reverse("formica:cycles"))
        self.assertContains(res, "completed")

    def test_proposals_and_detail_pages(self):
        colony = make_colony()
        cycle = ColonyCycle.objects.create(colony=colony, number=1)
        proposal = FormicaProposal.objects.create(
            colony=colony, cycle=cycle,
            ops={"ops": [_op("o1", "create_edge",
                             {"edge_type_slug": "knows", "from_uid": "a", "to_uid": "b"})]},
            summary={"total_ops": 1, "by_action": {"create_edge": 1}, "errors": 0},
        )
        res = self.client.get(reverse("formica:proposals"))
        self.assertContains(res, "create_edge")
        res = self.client.get(reverse("formica:proposal_detail", args=[proposal.pk]))
        self.assertContains(res, "formica-ops")


class ApiTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="T", author="T", publication_year=2024, active=True)
        make_bento_templates()
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")

    @override_settings(RAVIOLI_ENABLED=True)
    def test_run_now_triggers_cycle(self):
        make_colony()
        with patch("toto.formica.tasks.run_colony_cycle.delay") as delay, \
             patch("toto.celery_utils.celery_available", return_value=True):
            res = self.client.post(reverse("formica:api_run_now"))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["queued"])
        delay.assert_called_once()

    @override_settings(RAVIOLI_ENABLED=True)
    def test_run_now_conflicts_when_cycle_in_flight(self):
        colony = make_colony()
        ColonyCycle.objects.create(colony=colony, number=1,
                                   status=ColonyCycle.STATUS_RUNNING)
        res = self.client.post(reverse("formica:api_run_now"))
        self.assertEqual(res.status_code, 409)

    @override_settings(RAVIOLI_ENABLED=False)
    def test_run_now_blocked_when_graph_disabled(self):
        make_colony()
        res = self.client.post(reverse("formica:api_run_now"))
        self.assertEqual(res.status_code, 503)

    def test_pause_and_trusted_toggles(self):
        colony = make_colony()
        res = self.client.post(reverse("formica:api_pause"))
        self.assertTrue(res.json()["paused"])
        res = self.client.post(reverse("formica:api_trusted"))
        self.assertTrue(res.json()["trusted"])
        colony.refresh_from_db()
        self.assertTrue(colony.paused)
        self.assertTrue(colony.trusted)

    def test_cycle_chart_shape(self):
        colony = make_colony()
        for i in range(3):
            ColonyCycle.objects.create(
                colony=colony, number=i + 1, status=ColonyCycle.STATUS_COMPLETED,
                stats={"ph_mass": i * 10.0, "quarantined": i},
            )
        res = self.client.get(reverse("formica:api_cycle_chart"))
        data = res.json()
        self.assertEqual(data["labels"], [1, 2, 3])
        mass = next(d for d in data["datasets"] if d["label"] == "Pheromone mass")
        self.assertEqual(mass["data"], [0.0, 10.0, 20.0])

    def test_pheromone_map_degrades_when_graph_disabled(self):
        res = self.client.get(reverse("formica:api_pheromone_map"))
        self.assertEqual(res.json()["nodes"], [])

    def test_op_approval_flow(self):
        colony = make_colony()
        cycle = ColonyCycle.objects.create(colony=colony, number=1)
        proposal = FormicaProposal.objects.create(
            colony=colony, cycle=cycle,
            ops={"ops": [
                _op("o1"),
                _op("o2", validation={"status": "error", "errors": ["bad"]}),
            ]},
        )
        url = reverse("formica:api_proposal_op", args=[proposal.pk, "o1"])
        res = self.client.post(url, json.dumps({"approval": "approved"}),
                               content_type="application/json")
        self.assertEqual(res.status_code, 200)
        proposal.refresh_from_db()
        self.assertEqual(proposal.op_list[0]["approval"], "approved")
        self.assertEqual(proposal.summary["approved"], 1)

        # invalid op cannot be approved
        url = reverse("formica:api_proposal_op", args=[proposal.pk, "o2"])
        res = self.client.post(url, json.dumps({"approval": "approved"}),
                               content_type="application/json")
        self.assertEqual(res.status_code, 400)

        # unknown op / bad approval value
        url = reverse("formica:api_proposal_op", args=[proposal.pk, "nope"])
        res = self.client.post(url, json.dumps({"approval": "approved"}),
                               content_type="application/json")
        self.assertEqual(res.status_code, 404)

    @override_settings(RAVIOLI_ENABLED=True)
    def test_apply_endpoint_approve_all(self):
        colony = make_colony()
        cycle = ColonyCycle.objects.create(colony=colony, number=1)
        proposal = FormicaProposal.objects.create(
            colony=colony, cycle=cycle, ops={"ops": [_op("o1")]},
        )
        with patch("toto.formica.services.apply.run",
                   return_value=({"done": {}}, [])) as apply_run, \
             patch("toto.formica.services.apply.approve_all_valid") as approve:
            res = self.client.post(
                reverse("formica:api_proposal_apply", args=[proposal.pk]),
                json.dumps({"approve_all": True}),
                content_type="application/json",
            )
        self.assertEqual(res.status_code, 200)
        approve.assert_called_once()
        apply_run.assert_called_once()

    @override_settings(RAVIOLI_ENABLED=True)
    def test_reject_all_closes_without_touching_graph(self):
        colony = make_colony()
        cycle = ColonyCycle.objects.create(colony=colony, number=1)
        proposal = FormicaProposal.objects.create(
            colony=colony, cycle=cycle, ops={"ops": [_op("o1", approval="approved")]},
        )
        with patch("toto.formica.services.apply.run") as apply_run:
            res = self.client.post(
                reverse("formica:api_proposal_apply", args=[proposal.pk]),
                json.dumps({"reject_all": True}),
                content_type="application/json",
            )
        apply_run.assert_not_called()
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, FormicaProposal.STATUS_REJECTED)

    @override_settings(RAVIOLI_ENABLED=True)
    def test_applied_proposal_conflicts(self):
        colony = make_colony()
        cycle = ColonyCycle.objects.create(colony=colony, number=1)
        proposal = FormicaProposal.objects.create(
            colony=colony, cycle=cycle, status=FormicaProposal.STATUS_APPLIED,
        )
        res = self.client.post(
            reverse("formica:api_proposal_apply", args=[proposal.pk]), "{}",
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 409)
