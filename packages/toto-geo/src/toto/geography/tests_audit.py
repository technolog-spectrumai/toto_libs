"""Geography on the audit chain (2026-10-06): who did what and what it cost,
and never where. Walks the doors the other suites walk, then reads every
record, usage event and ledger description that was written.

    manage.py test toto.geography.tests_audit
"""

import json
import re
from unittest import mock, skipUnless

from django.apps import apps
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.geography import audit
from toto.geography.models import GeographyUsageEvent
from toto.geography.testing import (SQUARE, Economy, client_of, community, fresh_cache, member,
                                    op, post)
from toto.geography.tests_places import GDANSK
from toto.geography.tests_places import answering as place_answering
from toto.geography.tests_routes import A, B, ROUTING
from toto.geography.tests_routes import answering as route_answering

#: A number with three or more decimals: what a coordinate looks like.
FLOAT = re.compile(r"-?\d{1,3}\.\d{3,}")
SECRETS = ("Gdansk", "Gdańsk", "gdansk", "Dluga", "54.35", "18.64", "52.22", "21.01",
           "LineString", "339.51", "205.8", "Harbour house", "ring twice", "The harbour",
           "Home", "third floor")


@skipUnless(apps.is_installed("toto.audit"), "no audit chain on this host")
@override_settings(LOCATIONS_GEOCODING={"enabled": True}, GEOGRAPHY_ROUTING=ROUTING)
class AuditTests(TestCase):
    def setUp(self):
        from toto.geography import places

        fresh_cache(self)
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        if apps.is_installed("toto.tariffs") and apps.is_installed("toto.mana"):
            Economy(self)
        patcher = mock.patch.object(places, "PROVIDER_LIMIT", 1000)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user, self.person = member("hugo")
        self.guild = community("Guild", head=self.person)
        self.client = client_of(self.user)
        self.slug = {"slug": self.guild.slug}

    def records(self, action=None):
        from toto.audit.models import AuditRecord

        rows = AuditRecord.objects.filter(action__startswith="GEOGRAPHY.")
        return rows.filter(action=action) if action else rows

    def walk(self):
        """Every door once, as the other suites use them."""
        with place_answering(GDANSK):
            post(self.client, reverse("geography:search"), {"q": "Gdansk Dluga", "op": op()})
        with route_answering():
            post(self.client, reverse("geography:route"),
                 {"from": A, "to": B, "mode": "car", "op": op()})
        home = {"lat": 52.2297, "lng": 21.0122, "name": "Home", "note": "third floor"}
        post(self.client, reverse("geography:my_address"), {**home, "op": op()})
        post(self.client, reverse("geography:my_address"), {**home, "note": "", "op": op()})
        hq = {"lat": 54.352, "lng": 18.6466, "name": "Harbour house", "note": "ring twice"}
        post(self.client, reverse("geography:headquarters", kwargs=self.slug), {**hq, "op": op()})
        post(self.client, reverse("geography:zone", kwargs=self.slug),
             {"name": "The harbour", "description": "", "outline": SQUARE, "op": op()})
        post(self.client, reverse("geography:my_address_clear"), {})
        post(self.client, reverse("geography:headquarters_clear", kwargs=self.slug), {})
        post(self.client, reverse("geography:zone_clear", kwargs=self.slug), {})

    def test_each_act_is_one_record_under_its_own_action(self):
        self.walk()
        counts = {action: self.records(action).count() for action in audit.ACTIONS}
        self.assertEqual(counts, {
            "GEOGRAPHY.SEARCH": 1, "GEOGRAPHY.ROUTE": 1,
            "GEOGRAPHY.ADDRESS.SAVED": 2, "GEOGRAPHY.ADDRESS.CLEARED": 1,
            "GEOGRAPHY.HEADQUARTERS.SAVED": 1, "GEOGRAPHY.HEADQUARTERS.CLEARED": 1,
            "GEOGRAPHY.ZONE.SAVED": 1, "GEOGRAPHY.ZONE.CLEARED": 1,
        })
        self.assertEqual(self.records().count(), sum(counts.values()))

    def test_a_record_holds_the_actor_the_metric_the_amount_and_the_outcome(self):
        self.walk()
        for record in self.records():
            with self.subTest(action=record.action):
                self.assertEqual(record.actor_user, self.user)
                self.assertEqual(record.app_label, "geography")
                self.assertTrue(record.success)
                self.assertLessEqual({"metric", "amount", "outcome"}, set(record.metadata))
                self.assertLessEqual(set(record.metadata),
                                     {"metric", "amount", "outcome", "community", "link"})
        metrics = {record.action: record.metadata["metric"] for record in self.records()
                   if record.metadata["outcome"] == "charged"}
        self.assertEqual(metrics["GEOGRAPHY.SEARCH"], "geography.lookup")
        self.assertEqual(metrics["GEOGRAPHY.ROUTE"], "geography.route")
        self.assertEqual(metrics["GEOGRAPHY.ZONE.SAVED"], "geography.zone")
        self.assertEqual(metrics["GEOGRAPHY.HEADQUARTERS.SAVED"], "geography.pin")
        saved = [record.metadata["metric"] for record in
                 self.records("GEOGRAPHY.ADDRESS.SAVED").order_by("sequence")]
        self.assertEqual(saved, ["geography.pin", "geography.note"])
        for record in self.records().filter(action__endswith=".CLEARED"):
            self.assertEqual((record.metadata["outcome"], record.metadata["amount"]),
                             ("removed", "0"))

    def test_a_save_holds_its_link_row_and_a_search_or_route_no_row_at_all(self):
        self.walk()
        for action in ("GEOGRAPHY.SEARCH", "GEOGRAPHY.ROUTE"):
            record = self.records(action).get()
            self.assertEqual((record.object_type, record.object_id), ("", ""))
            self.assertNotIn("link", record.metadata)
            self.assertNotIn("community", record.metadata)
        for record in self.records().exclude(action__in=("GEOGRAPHY.SEARCH", "GEOGRAPHY.ROUTE")):
            self.assertIn(record.object_type, ("geography.personaddress",
                                               "geography.communityheadquarters"))
            self.assertEqual(str(record.metadata["link"]), record.object_id)
        for record in self.records().filter(action__startswith="GEOGRAPHY.ZONE"):
            self.assertEqual(record.metadata["community"], self.guild.slug)
        for record in self.records().filter(action__startswith="GEOGRAPHY.ADDRESS"):
            self.assertNotIn("community", record.metadata)

    def test_the_amount_is_what_the_ledger_took(self):
        if not (apps.is_installed("toto.tariffs") and apps.is_installed("toto.mana")):
            self.skipTest("this host bills nothing")
        self.walk()
        for record in self.records().filter(metadata__outcome="charged"):
            self.assertRegex(record.metadata["amount"], r"^[1-9]\d*$", record.action)

    def test_no_record_holds_a_coordinate_a_query_a_line_a_distance_or_a_duration(self):
        self.walk()
        self.assertGreaterEqual(self.records().count(), 9)
        for record in self.records():
            text = json.dumps({
                "action": record.action, "object_type": record.object_type,
                "object_id": record.object_id, "description": record.object_description,
                "changes": record.changes, "metadata": record.metadata,
                "source": record.source, "request_source": record.request_source,
            }, default=str, ensure_ascii=False)
            with self.subTest(action=record.action):
                self.assertIsNone(FLOAT.search(text), text)
                for secret in SECRETS:
                    self.assertNotIn(secret, text)

    def test_no_usage_event_or_ledger_description_holds_one_either(self):
        self.walk()
        rows = [(event.source_label, event.source_type, event.source_id,
                 event.idempotency_key, event.metadata) for event in
                GeographyUsageEvent.objects.all()]
        self.assertGreaterEqual(len(rows), 6)
        for event in GeographyUsageEvent.objects.all():
            self.assertEqual(set(event.metadata), {"request"})
            self.assertRegex(event.metadata["request"], r"^[0-9a-f]{64}$")
        digests = [event.metadata["request"] for event in GeographyUsageEvent.objects.all()]
        self.assertEqual(len(set(digests)), len(digests), "two records never share a digest")
        if apps.is_installed("toto.tariffs"):
            from toto.assets.models import LedgerTransaction
            from toto.tariffs.models import UsageRecord

            for model in (UsageRecord, LedgerTransaction):
                for row in model.objects.all():
                    rows.append({field.name: getattr(row, field.attname)
                                 for field in row._meta.concrete_fields
                                 if field.get_internal_type() in ("CharField", "TextField",
                                                                  "JSONField")})
        text = json.dumps(rows, default=str, ensure_ascii=False)
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    def test_a_refused_request_leaves_no_record(self):
        from toto.geography.testing import no_funds

        with no_funds(), place_answering(GDANSK):
            post(self.client, reverse("geography:search"), {"q": "Gdansk", "op": op()})
            post(self.client, reverse("geography:my_address"),
                 {"lat": 1, "lng": 2, "name": "", "note": "", "op": op()})
        post(self.client, reverse("geography:route"),
             {"from": {"address": 7}, "to": B, "mode": "car", "op": op()})
        self.assertEqual(self.records().count(), 0)

    def test_the_writer_is_guarded(self):
        with mock.patch("toto.audit.services.record", side_effect=RuntimeError("chain down")), \
                self.assertLogs("toto.geography.audit", "ERROR"):
            self.assertIsNone(audit.record(audit.SEARCH, self.user, metric="geography.lookup"))
        with self.modify_settings(INSTALLED_APPS={"remove": ["toto.audit"]}):
            self.assertIsNone(audit.record(audit.SEARCH, self.user))
