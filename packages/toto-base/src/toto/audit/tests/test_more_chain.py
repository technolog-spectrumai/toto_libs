"""The chain's own rules, beyond the first suite (2026-09-29): who the actor is,
what a row may carry, which chain a host writes to, the console verifier, the
admin's refusals, the context a request carries, and an actor kept on delete."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from io import StringIO
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import CommandError, call_command
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

import toto.audit as audit_api
from toto.audit.admin import AuditChainAdmin, AuditRecordAdmin
from toto.audit.context import AuditContext, current_context, reset_context, set_context
from toto.audit.middleware import AuditContextMiddleware, FileAuditMiddleware
from toto.audit.models import DEFAULT_CHAIN_KEY, AuditChain, AuditRecord, chain_key
from toto.audit.services import (
    SYSTEM,
    AuditVerification,
    record,
    request_source,
    sanitize,
    snapshot_fields,
    verify_chain,
)
from toto.core.models import Platform

User = get_user_model()


class ambient:
    def __init__(self, **kwargs):
        self.value = AuditContext(**kwargs)

    def __enter__(self):
        self.token = set_context(self.value)
        return self.value

    def __exit__(self, *exc):
        reset_context(self.token)


class SanitizeTests(SimpleTestCase):
    def test_values_that_json_cannot_carry_become_strings(self):
        when = dt.datetime(2026, 9, 29, 12, 30, tzinfo=dt.timezone.utc)
        ident = uuid.UUID("12345678-1234-5678-1234-567812345678")
        out = sanitize({"when": when, "day": dt.date(2026, 9, 29), "amount": Decimal("1.10"),
                        "id": ident, "path": PurePosixPath("/srv/a.txt")})
        self.assertEqual(out, {"when": "2026-09-29T12:30:00+00:00", "day": "2026-09-29",
                               "amount": "1.10", "id": str(ident), "path": "/srv/a.txt"})

    def test_plain_json_scalars_pass_through_unchanged(self):
        self.assertEqual(sanitize({"n": 3, "f": 1.5, "b": False, "none": None}),
                         {"n": 3, "f": 1.5, "b": False, "none": None})

    def test_sets_and_tuples_become_lists(self):
        self.assertEqual(sanitize({"t": (1, 2)})["t"], [1, 2])
        self.assertEqual(sanitize({"s": {"only"}})["s"], ["only"])

    def test_an_unknown_object_is_its_clamped_text(self):
        class Blob:
            def __str__(self):
                return "b" * 5000

        self.assertEqual(sanitize(Blob()), "b" * 1000)

    def test_key_names_are_matched_whatever_their_case_or_decoration(self):
        out = sanitize({"API_TOKEN": "x", "X-CSRFToken": "y", "sessionid": "z",
                        "Cookie": "c", "client_secret": "s", "Authorization": "a",
                        "username": "kept"})
        self.assertEqual({k for k, v in out.items() if v == "[REDACTED]"},
                         {"API_TOKEN", "X-CSRFToken", "sessionid", "Cookie",
                          "client_secret", "Authorization"})
        self.assertEqual(out["username"], "kept")

    def test_a_secret_written_into_a_sentence_is_redacted(self):
        for text in ("Authorization: Bearer abc", "token=abc&x=1",
                     "-----BEGIN private_key-----", "SECRET=shh"):
            with self.subTest(text=text):
                self.assertEqual(sanitize(text), "[REDACTED]")

    def test_secrets_inside_lists_of_dicts_are_redacted(self):
        out = sanitize({"rows": [{"name": "a", "password": "p"}, {"name": "b"}]})
        self.assertEqual(out["rows"], [{"name": "a", "password": "[REDACTED]"}, {"name": "b"}])

    def test_a_sensitive_key_redacts_its_whole_subtree(self):
        self.assertEqual(sanitize({"credentials": {"user": "a", "pin": 1}})["credentials"],
                         "[REDACTED]")


class RequestSourceTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_no_request_is_no_source(self):
        self.assertEqual(request_source(None), {})

    def test_a_uuid_in_the_path_never_reaches_the_row(self):
        """A recovery link carries its bearer secret as a uuid path segment."""
        secret = "0f8fad5b-d9cb-469f-a165-70867728950e"
        source = request_source(self.rf.get(f"/sso/recover/{secret}/"))
        self.assertEqual(source["path"], "/sso/recover/[uuid]/")
        self.assertNotIn(secret, str(source))

    @override_settings(TRUSTED_PROXIES=["172.16.0.0/12"])
    def test_a_forged_forwarded_for_loses_to_the_proxy_s_real_ip(self):
        """nginx appends to X-Forwarded-For, so its first entry is the client's
        own word; X-Real-IP is nginx's, believed from a trusted proxy (2026-09-30)."""
        request = self.rf.get("/", REMOTE_ADDR="172.18.0.5",
                              HTTP_X_FORWARDED_FOR="198.51.100.66, 203.0.113.7",
                              HTTP_X_REAL_IP="203.0.113.7")
        self.assertEqual(request_source(request)["ip_address"], "203.0.113.7")

    @override_settings(TRUSTED_PROXIES=["172.16.0.0/12"])
    def test_without_a_trusted_proxy_the_peer_is_the_address(self):
        request = self.rf.get("/", REMOTE_ADDR="192.0.2.9",
                              HTTP_X_FORWARDED_FOR="198.51.100.66",
                              HTTP_X_REAL_IP="203.0.113.7")
        self.assertEqual(request_source(request)["ip_address"], "192.0.2.9")
        direct = self.rf.get("/", REMOTE_ADDR="10.0.0.9")
        self.assertEqual(request_source(direct)["ip_address"], "10.0.0.9")

    def test_an_endless_user_agent_is_clamped(self):
        source = request_source(self.rf.get("/", HTTP_USER_AGENT="u" * 2000))
        self.assertEqual(len(source["user_agent"]), 500)

    def test_a_query_string_is_not_part_of_the_path(self):
        source = request_source(self.rf.get("/sso/login/?next=/x&token=abc"))
        self.assertEqual(source["path"], "/sso/login/")


class RequestSourceHistoryTests(TestCase):
    def test_a_row_written_the_old_way_still_verifies(self):
        """Rows that named the first X-Forwarded-For entry keep it: the digest
        covers request_source, so rewriting them would break the chain."""
        forged = RequestFactory().get("/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="198.51.100.66")
        old_shape = {**request_source(forged), "ip_address": "198.51.100.66"}
        with mock.patch("toto.audit.services.request_source", return_value=old_shape):
            record("OLD", app_label="t", request=forged)
        record("NEW", app_label="t", request=forged)
        rows = AuditRecord.objects.filter(action__in=["OLD", "NEW"]).order_by("sequence")
        self.assertEqual([row.request_source["ip_address"] for row in rows],
                         ["198.51.100.66", "127.0.0.1"])
        self.assertTrue(verify_chain().ok)


class ActorTests(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")

    def test_with_no_actor_given_the_request_s_user_is_the_actor(self):
        with ambient(user=self.ada, source="web", correlation_id="abc"):
            entry = record("DID_IT", app_label="t")
        self.assertEqual(entry.actor_user, self.ada)
        self.assertEqual(entry.actor_username, "ada")
        self.assertEqual(entry.source, "web")
        self.assertEqual(entry.correlation_id, "abc")

    def test_a_named_actor_wins_over_the_request_s_user(self):
        with ambient(user=self.ada):
            entry = record("DID_IT", app_label="t", actor_user=self.bob)
        self.assertEqual(entry.actor_user, self.bob)

    def test_the_system_sentinel_is_no_actor_even_inside_a_request(self):
        with ambient(user=self.ada, source="web"):
            entry = record("SWEEP", app_label="t", actor_user=SYSTEM)
        self.assertIsNone(entry.actor_user_id)
        self.assertEqual(entry.actor_username, "")
        self.assertIs(audit_api.system_actor(), SYSTEM)

    def test_an_anonymous_visitor_is_no_actor(self):
        with ambient(user=AnonymousUser()):
            entry = record("LOOKED", app_label="t")
        self.assertIsNone(entry.actor_user_id)

    def test_outside_any_request_the_source_is_the_system(self):
        entry = record("NIGHTLY", app_label="t")
        self.assertEqual(entry.source, "system")
        self.assertEqual(entry.correlation_id, "")

    def test_explicit_source_and_correlation_win_over_the_context(self):
        with ambient(user=self.ada, source="web", correlation_id="ctx"):
            entry = record("X", app_label="t", source="console", correlation_id="mine")
        self.assertEqual((entry.source, entry.correlation_id), ("console", "mine"))

    def test_the_context_s_request_is_the_source_when_none_is_passed(self):
        request = RequestFactory().post("/vault/upload/", REMOTE_ADDR="192.0.2.5")
        with ambient(user=self.ada, request=request):
            entry = record("UPLOAD", app_label="vault")
        self.assertEqual(entry.request_source["path"], "/vault/upload/")
        self.assertEqual(entry.request_source["ip_address"], "192.0.2.5")


class ActorKeptOnDeleteTests(TestCase):
    """The digest covers actor_user_id: deleting an account must not rewrite it."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test", author="Tests", publication_year=2026,
                                active=True)
        cls.keeper = User.objects.create_user("keeper", password="pw", is_staff=True)

    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")
        self.entry = record("EDITED", app_label="t", actor_user=self.ada, description="doc")
        self.ada_pk = self.ada.pk
        self.ada.delete()

    def test_the_actor_s_id_and_name_survive_as_they_were_written(self):
        row = AuditRecord.objects.get(pk=self.entry.pk)
        self.assertEqual(row.actor_user_id, self.ada_pk)
        self.assertEqual(row.actor_username, "ada")
        self.assertEqual(row.record_hash, self.entry.record_hash)
        self.assertTrue(verify_chain().ok)

    def test_a_joined_read_finds_no_account_rather_than_failing(self):
        row = AuditRecord.objects.select_related("actor_user").get(pk=self.entry.pk)
        self.assertIsNone(row.actor_user)

    def test_the_trail_pages_still_render_the_departed_actor(self):
        self.client.force_login(self.keeper)
        index = self.client.get(reverse("audit:index"))
        self.assertContains(index, "ada")
        detail = self.client.get(reverse("audit:detail", args=[self.entry.pk]))
        self.assertContains(detail, "ada")


class RowShapeTests(TestCase):
    def test_the_action_is_upper_cased_and_clamped(self):
        entry = record("a" * 150, app_label="t")
        self.assertEqual(entry.action, "A" * 100)

    def test_without_an_object_or_a_label_the_app_is_the_system(self):
        self.assertEqual(record("X").app_label, "system")

    def test_named_identity_overrides_what_the_object_would_say(self):
        platform = Platform.objects.create(site_name="Site", author="a", publication_year=2026)
        entry = record("X", obj=platform, object_type="custom.kind", object_id="42",
                       description="named")
        self.assertEqual((entry.object_type, entry.object_id, entry.object_description),
                         ("custom.kind", "42", "named"))
        self.assertEqual(entry.app_label, "core")

    def test_a_given_change_set_is_redacted_like_everything_else(self):
        entry = record("X", app_label="t",
                       changes={"password": {"before": "a", "after": "b"}, "name": "n"})
        self.assertEqual(entry.changes, {"password": "[REDACTED]", "name": "n"})

    def test_a_changed_secret_never_lands_in_the_change_set(self):
        entry = record("X", app_label="t", before={"password": "old-one"},
                       after={"password": "new-one"})
        self.assertNotIn("old-one", str(entry.changes))
        self.assertNotIn("new-one", str(entry.changes))

    def test_a_given_timestamp_is_kept_and_still_verifies(self):
        when = timezone.now() - dt.timedelta(days=3)
        entry = record("BACKDATED", app_label="t", timestamp=when)
        self.assertEqual(entry.timestamp, when)
        self.assertTrue(verify_chain().ok)

    def test_the_row_reads_as_its_sequence_action_and_outcome(self):
        ok = record("DONE", app_label="t")
        bad = record("REFUSED", app_label="t", success=False)
        self.assertEqual(str(ok), "#1 DONE [OK]")
        self.assertEqual(str(bad), "#2 REFUSED [FAIL]")
        self.assertEqual(str(ok.chain), ok.chain.name)

    def test_change_and_event_are_record_by_other_names(self):
        platform = Platform.objects.create(site_name="Old", author="a", publication_year=2026)
        changed = audit_api.change("RENAMED", obj=platform, before={"site_name": "Old"},
                                   after={"site_name": "New"})
        self.assertEqual(changed.changes, {"site_name": {"before": "Old", "after": "New"}})
        happened = audit_api.event("HAPPENED", app_label="t", metadata={"n": 1})
        self.assertEqual(happened.sequence, changed.sequence + 1)
        self.assertEqual(audit_api.record("THIRD", app_label="t").sequence, 3)


class SnapshotTests(SimpleTestCase):
    def test_no_instance_is_no_snapshot(self):
        self.assertEqual(snapshot_fields(None, ["a"]), {})

    def test_values_are_text_and_missing_ones_are_none(self):
        thing = SimpleNamespace(count=3, name="x", gone=None)
        self.assertEqual(snapshot_fields(thing, ["count", "name", "gone", "absent"]),
                         {"count": "3", "name": "x", "gone": None, "absent": None})


class ChainKeyTests(TestCase):
    @override_settings(AUDIT_CHAIN_KEY="")
    def test_with_no_host_key_the_chain_is_the_historical_one(self):
        """A live chain was written under this exact string before the move."""
        self.assertEqual(chain_key(), DEFAULT_CHAIN_KEY)
        self.assertEqual(record("X", app_label="t").chain.key, "placidia-activity")

    @override_settings(AUDIT_CHAIN_KEY="zenobia-test")
    def test_a_host_key_opens_its_own_chain_from_sequence_one(self):
        with override_settings(AUDIT_CHAIN_KEY="another-host"):
            record("ON_THE_OLD_CHAIN", app_label="t")
            record("AGAIN", app_label="t")
        entry = record("FIRST_HERE", app_label="t")
        self.assertEqual(entry.chain.key, "zenobia-test")
        self.assertEqual(entry.sequence, 1)
        self.assertEqual(entry.previous_hash, "")
        self.assertEqual(verify_chain().checked, 1)

    def test_a_chain_can_be_verified_by_name(self):
        with override_settings(AUDIT_CHAIN_KEY="elsewhere"):
            record("X", app_label="t")
            record("Y", app_label="t")
        chain = AuditChain.objects.get(key="elsewhere")
        self.assertEqual(verify_chain(chain).checked, 2)
        self.assertEqual(verify_chain().checked, 0)

    def test_a_host_with_no_chain_yet_verifies_empty(self):
        self.assertEqual(verify_chain(), AuditVerification(True, 0))

    def test_a_broken_verdict_says_so(self):
        self.assertEqual(AuditVerification(False, 2, 3, "x").status, "broken")


class VerifyCommandTests(TestCase):
    def test_a_healthy_chain_is_reported_with_its_count(self):
        record("ONE", app_label="t")
        record("TWO", app_label="t")
        out = StringIO()
        call_command("verify_audit", stdout=out)
        self.assertIn("healthy", out.getvalue())
        self.assertIn("2 record(s)", out.getvalue())

    def test_a_broken_chain_fails_the_command_and_names_the_break(self):
        for n in range(3):
            record(f"R{n}", app_label="t")
        AuditRecord.objects.filter(sequence=2).update(object_description="rewritten")
        with self.assertRaises(CommandError) as caught:
            call_command("verify_audit", stdout=StringIO())
        self.assertIn("sequence 2", str(caught.exception))
        self.assertIn("1 record(s) verified", str(caught.exception))


class AdminRefusalTests(TestCase):
    def test_nobody_adds_edits_or_deletes_a_record_in_the_admin(self):
        root = User.objects.create_superuser("root", password="pw")
        request = RequestFactory().get("/admin/")
        request.user = root
        admin = AuditRecordAdmin(AuditRecord, AdminSite())
        entry = record("X", app_label="t")
        self.assertFalse(admin.has_add_permission(request))
        self.assertFalse(admin.has_change_permission(request, entry))
        self.assertFalse(admin.has_delete_permission(request, entry))

    def test_the_chain_admin_keeps_its_creation_time_read_only(self):
        self.assertIn("created_at", AuditChainAdmin(AuditChain, AdminSite()).readonly_fields)


class ContextMiddlewareTests(TestCase):
    def test_the_request_s_user_and_a_fresh_correlation_id_are_ambient(self):
        seen = []
        request = RequestFactory().get("/")
        request.user = User.objects.create_user("ada", password="pw")

        def view(req):
            seen.append(current_context())
            return HttpResponse()

        middleware = AuditContextMiddleware(view)
        middleware(request)
        middleware(request)
        first, second = seen
        self.assertEqual(first.user, request.user)
        self.assertIs(first.request, request)
        self.assertEqual(first.source, "web")
        self.assertRegex(first.correlation_id, r"^[0-9a-f]{32}$")
        self.assertNotEqual(first.correlation_id, second.correlation_id)
        self.assertIsNone(current_context())

    def test_the_context_is_cleared_even_when_the_view_raises(self):
        def view(req):
            raise RuntimeError("boom")

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        with self.assertRaises(RuntimeError):
            AuditContextMiddleware(view)(request)
        self.assertIsNone(current_context())


class FileMiddlewareUnitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("filer", password="pw")
        self.rf = RequestFactory()

    def vault_rows(self):
        # Creating the user above is itself on the chain (AUTH.ACCOUNT_CREATED).
        return AuditRecord.objects.filter(app_label="vault")

    def _call(self, request, name, *, namespace="vault", status=200, **kwargs):
        request.user = self.user
        request.resolver_match = SimpleNamespace(namespace=namespace, url_name=name,
                                                 kwargs=kwargs)
        return FileAuditMiddleware(lambda r: HttpResponse(status=status))(request)

    def test_a_request_outside_the_vault_is_not_a_file_operation(self):
        self._call(self.rf.post("/x/"), "delete_file", namespace="forum")
        self.assertFalse(self.vault_rows().exists())

    def test_an_unresolved_request_is_not_a_file_operation(self):
        request = self.rf.post("/x/")
        request.user = self.user
        FileAuditMiddleware(lambda r: HttpResponse())(request)
        self.assertFalse(self.vault_rows().exists())

    def test_a_lock_claim_reaches_the_chain_only_when_refused(self):
        # 2026-09-30: 403 (may read, may not write) and 404 (may not see) are
        # somebody reaching for a file that is not theirs; 200 and 423 (a
        # colleague is editing) are an editor opening, every page load.
        for status in (200, 423, 405):
            self._call(self.rf.post("/v/"), "lock_acquire", status=status, pk=9)
        self.assertFalse(self.vault_rows().exists())
        for status in (403, 404):
            self._call(self.rf.post("/v/"), "lock_acquire", status=status, pk=9)
        rows = self.vault_rows().order_by("sequence")
        self.assertEqual([(r.action, r.success, r.object_id, r.metadata["status"])
                          for r in rows],
                         [("FILE_LOCK_REFUSED", False, "9", 403),
                          ("FILE_LOCK_REFUSED", False, "9", 404)])

    def test_heartbeats_and_status_polls_never_reach_the_chain(self):
        for name in ("lock_heartbeat", "zip_status", "transfer_status"):
            self._call(self.rf.post("/v/"), name)
        self.assertFalse(self.vault_rows().exists())

    def test_a_file_named_in_the_body_is_the_record_s_object(self):
        self._call(self.rf.post("/v/", {"file_pk": "77"}), "delete_file")
        entry = self.vault_rows().get()
        self.assertEqual((entry.action, entry.object_id, entry.object_type),
                         ("FILE_DELETED", "77", "VaultFile"))
        self.assertEqual(entry.actor_user, self.user)

    def test_a_bucket_door_is_recorded_against_the_vault(self):
        self._call(self.rf.post("/v/"), "bucket_settings", bucket_slug="notes")
        entry = self.vault_rows().get()
        self.assertEqual((entry.action, entry.object_type, entry.object_id),
                         ("VAULT_ACTION", "Vault", "notes"))

    def test_a_download_that_failed_is_recorded_as_failed(self):
        self._call(self.rf.get("/v/"), "api_file_download", status=404, file_pk=5)
        entry = self.vault_rows().get()
        self.assertFalse(entry.success)
        self.assertEqual(entry.metadata, {"url_name": "api_file_download", "method": "GET",
                                          "status": 404})

    def test_a_chain_that_cannot_write_never_fails_the_file_operation(self):
        with mock.patch("toto.audit.record", side_effect=RuntimeError("down")), \
                self.assertLogs("toto.audit.middleware", "ERROR"):
            response = self._call(self.rf.post("/v/"), "delete_file")
        self.assertEqual(response.status_code, 200)


class TrailFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test", author="Tests", publication_year=2026,
                                active=True)
        cls.keeper = User.objects.create_user("keeper", password="pw", is_staff=True)
        record("FILE_UPLOADED", app_label="vault", object_id="501", description="alpha.txt")
        record("ROOM_RENAMED", app_label="forum", object_id="9", description="beta room")

    def setUp(self):
        self.client.force_login(self.keeper)

    def rows(self, **query):
        response = self.client.get(reverse("audit:index"), query)
        self.assertEqual(response.status_code, 200)
        return [r.object_description for r in response.context["page_obj"]]

    def test_the_app_filter_keeps_one_app(self):
        self.assertEqual(self.rows(app="forum"), ["beta room"])

    def test_the_action_filter_ignores_case(self):
        self.assertEqual(self.rows(action="file_uploaded"), ["alpha.txt"])

    def test_the_search_matches_an_object_id_exactly_and_names_loosely(self):
        self.assertEqual(self.rows(q="501"), ["alpha.txt"])
        self.assertEqual(self.rows(q="50"), [])
        self.assertEqual(self.rows(q="BETA"), ["beta room"])
