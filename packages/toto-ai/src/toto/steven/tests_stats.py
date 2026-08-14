"""The statistics: recorded on every run, aggregated for staff, scoped for users.

The spec sentence under test: **"Never record API keys or unnecessary prompt
contents."** The positive half is the metadata that IS recorded — duration,
agent, provider, model, tokens, outcome — and the negative half is asserted
with sentinels: content columns exist on the run (the worker needs them) but
no statistics rendering ever selects or shows them.
"""

import json

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

from . import dispatch, services, stats
from .client import ProviderError
from .models import AiAgent, AiProvider, AiRun, RunStatus
from .vault import vault

User = get_user_model()

PASSPHRASE = "a-test-passphrase-that-is-long-enough"


def _platform():
    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})


def _answer(text="fine words"):
    return {"text": text,
            "usage": {"prompt_tokens": 10, "completion_tokens": 20,
                      "total_tokens": 30},
            "model": "gpt-4.1-mini"}


@override_settings(STEVEN_VAULT_PASSWORD=PASSPHRASE)
class RecordingTests(TestCase):
    """Every finished run carries its duration and its label snapshots."""

    def setUp(self):
        _platform()
        vault.clear_cache()
        from .tests import _register_test_surfaces
        _register_test_surfaces()
        self.user = User.objects.create_user("timed", password="pw")
        self.provider = AiProvider.objects.create(label="the-provider",
                                                  active=True)
        self.provider.secret = vault.store_secret("sk-test", name="stats-key")
        self.provider.save(update_fields=["secret"])
        AiAgent.objects.create(name="Steven", active=True)

    def _run(self, surface="tests"):
        return dispatch.create_run(user=self.user, surface=surface,
                                   action="improve", source_text="words")

    def test_a_success_records_duration_and_both_labels(self):
        run = self._run()

        with mock.patch("toto.steven.client.complete",
                        return_value=_answer()):
            services.execute(run)

        run.refresh_from_db()
        self.assertEqual(run.agent_label, "Steven")
        self.assertEqual(run.provider_label, "the-provider")
        self.assertGreaterEqual(run.duration_ms, 0)

    def test_a_provider_failure_records_its_duration_too(self):
        """A timeout that took the whole timeout to happen is a diagnostic
        fact, not an absence of one."""
        run = self._run()

        with mock.patch("toto.steven.client.complete",
                        side_effect=ProviderError("no")):
            services.execute(run)

        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(run.provider_label, "the-provider")
        self.assertGreaterEqual(run.duration_ms, 0)

    def test_a_scanner_refusal_records_the_full_round_trip(self):
        run = self._run(surface="tests-html")
        refusing = mock.Mock(ok=False, reason="declaration", detail="script")

        with mock.patch("toto.steven.client.complete",
                        return_value=_answer("<script>")), \
             mock.patch("toto.vault.scanning.scan", return_value=refusing):
            services.execute(run)

        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(run.agent_label, "Steven")
        self.assertGreaterEqual(run.duration_ms, 0)

    def test_no_active_agent_means_an_empty_label(self):
        AiAgent.objects.update(active=False)
        run = self._run()

        with mock.patch("toto.steven.client.complete",
                        return_value=_answer()):
            services.execute(run)

        run.refresh_from_db()
        self.assertEqual(run.agent_label, "")


class ScopingTests(TestCase):
    """Staff see everyone; a user sees exactly themselves; content sees nobody."""

    def setUp(self):
        _platform()
        self.staff = User.objects.create_user("op", password="pw",
                                              is_staff=True)
        self.alice = User.objects.create_user("alice", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        for owner, n in ((self.alice, 2), (self.bob, 3)):
            for _ in range(n):
                AiRun.objects.create(
                    owner=owner, surface="tests", action="improve",
                    source_text="SENTINEL_SOURCE",
                    instruction="SENTINEL_INSTRUCTION",
                    result="SENTINEL_RESULT",
                    status=RunStatus.SUCCESS, total_tokens=10,
                    agent_label="Steven", model_used="gpt-4.1-mini")

    def test_staff_aggregate_counts_every_owner(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("steven:manage")
                                   + "?tab=statistics")

        self.assertEqual(response.context["stats"]["runs"], 5)
        self.assertContains(response, "alice")
        self.assertContains(response, "bob")

    def test_the_aggregate_never_shows_prompt_contents(self):
        """The stats layer never SELECTS the content columns, so no template
        fed from it can leak them — asserted, not hoped."""
        self.client.force_login(self.staff)

        response = self.client.get(reverse("steven:manage")
                                   + "?tab=statistics")

        for sentinel in (b"SENTINEL_SOURCE", b"SENTINEL_INSTRUCTION",
                         b"SENTINEL_RESULT"):
            self.assertNotIn(sentinel, response.content)

    def test_a_user_sees_their_own_numbers_only(self):
        self.client.force_login(self.alice)

        response = self.client.get(reverse("steven:console"))

        self.assertEqual(response.context["stats"]["runs"], 2)
        self.assertNotIn(b"SENTINEL_SOURCE", response.content)

    def test_an_ordinary_user_cannot_open_the_aggregate(self):
        self.client.force_login(self.alice)

        response = self.client.get(reverse("steven:manage")
                                   + "?tab=statistics")

        self.assertEqual(response.status_code, 403)

    def test_recent_rows_carry_metadata_columns_only(self):
        rows = stats.recent(AiRun.objects.all())

        self.assertTrue(rows)
        forbidden = {"source_text", "instruction", "result"}
        for row in rows:
            self.assertFalse(forbidden & set(row))


class BucketTests(TestCase):
    """The Meta-ordering GROUP BY trap, pinned."""

    def setUp(self):
        _platform()
        self.user = User.objects.create_user("bucketed", password="pw")

    def test_two_runs_on_one_day_are_one_bucket(self):
        """AiRun.Meta orders by -created_at; an aggregate without an explicit
        order_by would fold that column into the GROUP BY and return one row
        per run."""
        for _ in range(2):
            AiRun.objects.create(owner=self.user, surface="s", action="a",
                                 status=RunStatus.SUCCESS, total_tokens=5)

        chart = json.loads(stats.requests_chart_json(AiRun.objects.all()))

        answered = next(d for d in chart["datasets"]
                        if d["label"] == "Answered")
        self.assertEqual(sum(answered["data"]), 2)
        self.assertEqual(max(answered["data"]), 2)

    def test_the_tokens_chart_folds_the_same_way(self):
        for _ in range(2):
            AiRun.objects.create(owner=self.user, surface="s", action="a",
                                 status=RunStatus.SUCCESS, total_tokens=7)

        chart = json.loads(stats.tokens_chart_json(AiRun.objects.all()))

        self.assertEqual(sum(chart["datasets"][0]["data"]), 14)
        self.assertEqual(max(chart["datasets"][0]["data"]), 14)

    def test_overview_success_rate_is_of_finished_runs(self):
        AiRun.objects.create(owner=self.user, surface="s", action="a",
                             status=RunStatus.SUCCESS)
        AiRun.objects.create(owner=self.user, surface="s", action="a",
                             status=RunStatus.FAILED)
        AiRun.objects.create(owner=self.user, surface="s", action="a",
                             status=RunStatus.PENDING)

        overview = stats.overview(AiRun.objects.all())

        self.assertEqual(overview["runs"], 3)
        self.assertEqual(overview["success_percent"], 50)
