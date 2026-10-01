"""monit's pages need a superuser on the Superuser plan (2026-10-01, 36.R2).

The superuser bit alone opened Monitoring, Database, Jobs and History until
then — the one superuser function left that did not ask for the plan
(`toto.subscriptions.models.superuser_plan_active`). Through the full
middleware stack: every page answers 403 to a superuser who has not taken the
plan, or who has lost it, and 200 to one who holds it.
"""

from __future__ import annotations

import io
from unittest import mock, skipUnless

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform

User = get_user_model()

PAGES = ("monit:overview", "monit:status", "monit:jobs", "monit:history")


@skipUnless(django_apps.is_installed("toto.subscriptions"), "no plan sold here")
class PlanGateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="T", author="T", publication_year=2026,
                                active=True)
        cls.root = User.objects.create_superuser("plan-root", "r@x.test", "pw")
        call_command("bootstrap_plans", stdout=io.StringIO())   # root → the Superuser plan
        cls.root = User.objects.get(pk=cls.root.pk)
        # Made after the bootstrap: a superuser who never took the plan.
        cls.bare_root = User.objects.create_superuser("bare-root", "b@x.test", "pw")

    def setUp(self):
        # The live panel measures this process, and the Jobs page asks for a
        # worker; neither reading is what these tests are about.
        for target, value in (("toto.monit.collectors.collect_system", {}),
                              ("toto.monit.collectors.collect_celery", {}),
                              ("toto.celery_utils.celery_available", False)):
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def statuses(self, user):
        self.client.force_login(user)
        return {name: self.client.get(reverse(name)).status_code for name in PAGES}

    def test_a_superuser_on_the_plan_opens_every_page(self):
        self.assertEqual(self.statuses(self.root), dict.fromkeys(PAGES, 200))

    def test_the_superuser_bit_alone_opens_none(self):
        self.assertEqual(self.statuses(self.bare_root), dict.fromkeys(PAGES, 403))

    def test_a_superuser_who_loses_the_plan_is_refused_at_the_next_page(self):
        """The plan is read live, as everywhere: no session keeps it."""
        from toto.subscriptions.models import Subscription

        self.client.force_login(self.root)
        self.assertEqual(self.client.get(reverse("monit:overview")).status_code, 200)
        Subscription.objects.filter(user=self.root).delete()
        self.assertEqual(self.statuses(self.root), dict.fromkeys(PAGES, 403))

    def test_the_tab_strip_offers_what_the_pages_open(self):
        """A tab is never shown to someone its page would refuse."""
        from toto.core.monitoring import monitoring_tabs

        monit = {"monitoring", "database", "jobs", "history"}
        self.assertTrue(monit <= {t["slug"] for t in monitoring_tabs(self.root)})
        self.assertFalse(monit & {t["slug"] for t in monitoring_tabs(self.bare_root)})
