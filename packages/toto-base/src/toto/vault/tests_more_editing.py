"""``toto.vault.editing`` — the door every rich editor asks before it opens
(primula and the text editor ride it), the two refusals on the save path, and
``settle`` after a save lands. The rule this module exists for: a person is
never refused a save for a document they were allowed to open.
"""

import json
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.base import ContentFile
from django.test import RequestFactory, TestCase, modify_settings, override_settings

from toto.quota.charge import InsufficientFunds
from toto.vault import editing, locks
from toto.vault.models import FileVersion, VaultFile, VaultQuotaPolicy, VaultUsageEvent

User = get_user_model()


def broke():
    return InsufficientFunds("ZEN", 100, 0, detail="Not enough ZEN for this save.")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-more-editing-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.writer = User.objects.create_user("writer", password="pw")
        cls.colleague = User.objects.create_user("colleague", password="pw")

    def file(self, body=b"v1", *, encrypted=False, content_hash="h1"):
        vault_file = VaultFile(owner=self.writer, title="sheet.json", key="sheet",
                               file_type="text", is_encrypted=encrypted,
                               content_hash=content_hash)
        vault_file.file.save("sheet.json", ContentFile(body), save=False)
        vault_file.save()
        return vault_file


class DoorOrderTests(_Fixture):
    def test_an_ordinary_writer_gets_an_open_writable_door(self):
        door = editing.door_for(self.writer, self.file(), entitlement="")
        self.assertEqual((door.open, door.writable, door.reason), (True, True, ""))
        self.assertEqual(door.as_context(), {"can_edit": True, "edit_reason": "",
                                             "edit_message": "", "locked_by": ""})

    @override_settings(VAULT_FILE_EDITS=False)
    def test_a_host_without_edits_answers_first_and_keeps_the_page_open(self):
        f = self.file(encrypted=True)
        locks.acquire(f, self.colleague)
        door = editing.door_for(self.writer, f, entitlement="primula")
        self.assertEqual((door.open, door.writable, door.reason), (True, False, "host"))

    def test_nobody_signed_out_is_let_in(self):
        door = editing.door_for(AnonymousUser(), self.file(), entitlement="")
        self.assertEqual((door.open, door.status, door.reason), (False, 402, "anonymous"))

    @modify_settings(MIDDLEWARE={"append": "toto.subscriptions.gate.SubscriptionGateMiddleware"})
    def test_a_plan_without_the_editor_closes_the_door(self):
        with patch("toto.subscriptions.gate.is_entitled", return_value=False) as asked:
            door = editing.door_for(self.writer, self.file(encrypted=True),
                                    entitlement="primula")
        asked.assert_called_once_with(self.writer, "primula")
        self.assertEqual((door.open, door.writable, door.status, door.reason),
                         (False, False, 402, "subscription-required"))

    @modify_settings(MIDDLEWARE={"remove": "toto.subscriptions.gate.SubscriptionGateMiddleware"})
    def test_without_the_gate_middleware_nothing_is_paywalled(self):
        # The host's switch for enforcement is the middleware, not the app.
        with patch("toto.subscriptions.gate.is_entitled", return_value=False) as asked:
            door = editing.door_for(self.writer, self.file(), entitlement="primula")
        asked.assert_not_called()
        self.assertTrue(door.writable)

    def test_an_encrypted_file_opens_read_only(self):
        door = editing.door_for(self.writer, self.file(encrypted=True), entitlement="")
        self.assertEqual((door.open, door.writable, door.reason), (True, False, "encrypted"))

    def test_a_colleague_in_the_file_is_named_before_anyone_types(self):
        f = self.file()
        locks.acquire(f, self.colleague)
        door = editing.door_for(self.writer, f, entitlement="")
        self.assertEqual((door.writable, door.reason, door.locked_by),
                         (False, "locked", "colleague"))
        self.assertIn("colleague is editing this file right now.", door.message)

    def test_my_own_lock_is_no_obstacle(self):
        f = self.file()
        locks.acquire(f, self.writer)
        self.assertTrue(editing.door_for(self.writer, f, entitlement="").writable)

    def test_an_unpaid_levy_opens_read_only_and_says_so(self):
        from toto.quota import InArrears

        with patch("toto.quota.api.check_quota", side_effect=InArrears()):
            door = editing.door_for(self.writer, self.file(), entitlement="",
                                    metric_code="primula.save", policy_model=VaultQuotaPolicy)
        self.assertEqual((door.open, door.writable, door.reason), (True, False, "in-arrears"))
        self.assertIn("Nothing you have stored has been deleted.", door.message)

    def test_an_empty_wallet_opens_read_only(self):
        with patch("toto.quota.charge.check_funds", side_effect=broke()):
            door = editing.door_for(self.writer, self.file(), entitlement="",
                                    metric_code="primula.save")
        self.assertEqual((door.open, door.writable, door.reason),
                         (True, False, "insufficient-funds"))
        self.assertEqual(door.message, "Not enough ZEN for this save.")

    def test_money_is_looked_up_under_the_metrics_own_app(self):
        with patch("toto.quota.charge.price_for", return_value=None) as priced:
            editing.door_for(self.writer, self.file(), entitlement="kanban",
                             metric_code="cyprian.save")
        priced.assert_called_once_with(self.writer, "cyprian")

    def test_no_metric_means_no_money_question(self):
        with patch("toto.quota.charge.check_funds", side_effect=broke()):
            self.assertTrue(editing.door_for(self.writer, self.file(), entitlement="").writable)


class ClosedResponseTests(_Fixture):
    def test_a_signed_out_refusal_is_json_with_its_reason(self):
        door = editing.door_for(AnonymousUser(), None, entitlement="")
        response = editing.closed_response(RequestFactory().get("/"), door)
        self.assertEqual(response.status_code, 402)
        self.assertEqual(json.loads(response.content)["reason"], "anonymous")

    def test_a_plan_refusal_is_the_gates_own_page(self):
        door = editing.Door(open=False, writable=False, status=402,
                            reason="subscription-required", entitlement="primula")
        request = RequestFactory().get("/", HTTP_ACCEPT="application/json")
        request.user = self.writer
        response = editing.closed_response(request, door)
        self.assertEqual(response.status_code, 402)
        payload = json.loads(response.content)
        self.assertEqual((payload["reason"], payload["entitlement"]),
                         ("subscription-required", "primula"))
        self.assertIn("plans_url", payload)


class SavePathTests(_Fixture):
    def test_a_free_or_own_file_is_not_refused(self):
        f = self.file()
        self.assertIsNone(editing.refuse_if_locked(f, self.writer))
        locks.acquire(f, self.writer)
        self.assertIsNone(editing.refuse_if_locked(f, self.writer))

    def test_somebody_elses_lock_is_a_423_naming_them(self):
        f = self.file()
        locks.acquire(f, self.colleague)
        response = editing.refuse_if_locked(f, self.writer, noun="sheet")
        self.assertEqual(response.status_code, 423)
        payload = json.loads(response.content)
        self.assertEqual(payload["locked_by"], "colleague")
        self.assertEqual(payload["error"], "colleague is editing this sheet.")

    def test_no_base_hash_or_a_matching_one_is_not_stale(self):
        f = self.file(content_hash="abc")
        self.assertIsNone(editing.refuse_if_stale(f, "", body=b"x", author=self.writer))
        self.assertIsNone(editing.refuse_if_stale(f, "abc", body=b"x", author=self.writer))
        unhashed = self.file(content_hash="")
        self.assertIsNone(editing.refuse_if_stale(unhashed, "zzz", body=b"x",
                                                  author=self.writer))

    def test_a_stale_save_is_409_and_the_losing_work_is_kept(self):
        f = self.file(content_hash="current")
        response = editing.refuse_if_stale(f, "old", body=b"my paragraph",
                                           author=self.colleague, noun="sheet")
        self.assertEqual(response.status_code, 409)
        payload = json.loads(response.content)
        self.assertEqual(payload["content_hash"], "current")
        draft = FileVersion.objects.get(file=f)
        self.assertEqual(payload["kept_as_version"], draft.number)
        self.assertTrue(draft.is_conflict)
        self.assertEqual(draft.read(), b"my paragraph")
        self.assertIn("colleague", str(draft.label))

    def test_a_rescue_that_fails_is_still_a_409_not_a_500(self):
        f = self.file(content_hash="current")
        with patch("toto.vault.versions.save_conflicting_draft", side_effect=OSError("disk")), \
                self.assertLogs("toto.vault.editing", "ERROR"):
            response = editing.refuse_if_stale(f, "old", body=b"x", author=self.writer)
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(json.loads(response.content)["kept_as_version"])


class SettleTests(_Fixture):
    def test_every_save_is_a_version(self):
        f = self.file(b"saved body")
        out = editing.settle(f, self.writer, metric_code="", label="lunch")
        self.assertEqual(out, {"version": 1})
        self.assertEqual(str(FileVersion.objects.get(file=f).label), "lunch")

    def test_a_metered_save_is_counted_once_against_the_file(self):
        f = self.file()
        editing.settle(f, self.writer, metric_code="primula.save", event_model=VaultUsageEvent)
        event = VaultUsageEvent.objects.get(metric_code="primula.save")
        self.assertEqual((event.user, event.source_id, event.source_label),
                         (self.writer, str(f.pk), "sheet.json"))

    def test_an_empty_wallet_warns_and_the_save_stands(self):
        f = self.file()
        with patch("toto.quota.charge.charge", side_effect=broke()):
            out = editing.settle(f, self.writer, metric_code="primula.save",
                                 event_model=VaultUsageEvent)
        self.assertEqual(out["billing_warning"], "Not enough ZEN for this save.")
        self.assertEqual(out["version"], 1)

    def test_a_charge_that_crashes_is_logged_and_the_save_stands(self):
        f = self.file()
        with patch("toto.quota.charge.charge", side_effect=RuntimeError("ledger")), \
                self.assertLogs("toto.vault.editing", "ERROR"):
            out = editing.settle(f, self.writer, metric_code="primula.save",
                                 event_model=VaultUsageEvent)
        self.assertNotIn("billing_warning", out)

    def test_no_event_written_means_no_charge(self):
        f = self.file()
        with patch("toto.quota.api.record_usage", return_value=None), \
                patch("toto.quota.charge.charge") as charged:
            editing.settle(f, self.writer, metric_code="primula.save",
                           event_model=VaultUsageEvent)
        charged.assert_not_called()

    def test_a_version_that_cannot_be_written_does_not_stop_the_count(self):
        f = self.file()
        with patch("toto.vault.versions.save_version", side_effect=OSError("disk")), \
                self.assertLogs("toto.vault.editing", "ERROR"):
            out = editing.settle(f, self.writer, metric_code="primula.save",
                                 event_model=VaultUsageEvent)
        self.assertNotIn("version", out)
        self.assertTrue(VaultUsageEvent.objects.filter(metric_code="primula.save").exists())
