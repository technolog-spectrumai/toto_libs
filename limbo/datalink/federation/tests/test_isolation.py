"""datalink's own tables must never leave this instance.

They hold peer credentials — a magic token, a hashed key we accept, and a plaintext key
we present. Two independent things keep them out of a backup archive, and both are
asserted here rather than trusted:

* no datalink model has a field named ``uid``, which is the only thing
  ``backup_engine.is_backup_model()`` looks at;
* every datalink model is declared refused in the replication registry, so datalink
  cannot replicate itself to a peer either.

The first matters because it is *structural*: a host that adds "datalink" to
``APPS_TO_SYNC`` — a plausible mistake, since every other app in the list belongs there
— would otherwise write its peer grants into a signed, pullable ZIP.
"""
from django.apps import apps
from django.test import TestCase

from toto.datalink.models import (
    DatalinkConflict,
    DatalinkGrant,
    DatalinkIdentityMap,
    DatalinkMergeBase,
    DatalinkPeer,
    DatalinkRun,
    DatalinkStageRun,
)
from toto.datalink.registry import IDENTITY_REFUSE, load_registry

ALL_MODELS = (
    DatalinkGrant, DatalinkPeer, DatalinkRun, DatalinkStageRun,
    DatalinkMergeBase, DatalinkConflict, DatalinkIdentityMap,
)


class BackupIsolationTests(TestCase):
    def test_no_datalink_model_has_a_field_named_uid(self):
        # is_backup_model() selects on exactly this name. Identity columns are
        # grant_uid / peer_uid / run_uid for that reason alone.
        offenders = []
        for model in apps.get_app_config("datalink").get_models():
            names = {f.name for f in model._meta.get_fields() if getattr(f, "concrete", False)}
            if "uid" in names:
                offenders.append(model._meta.label)
        self.assertEqual(offenders, [])

    def test_the_backup_engine_would_not_select_any_datalink_model(self):
        # Assert against the real selector rather than reimplementing its rule, so this
        # keeps working if the rule changes.
        from toto.backup.services.backup_engine import BackupEngine

        engine = BackupEngine(platform=None, apps_to_sync=[], sign=False)
        for model in apps.get_app_config("datalink").get_models():
            with self.subTest(model=model._meta.label):
                self.assertFalse(engine.is_backup_model(model))

    def test_every_datalink_model_is_refused_by_the_registry(self):
        registry = load_registry()
        for model in apps.get_app_config("datalink").get_models():
            with self.subTest(model=model._meta.label):
                policy = registry.get(model._meta.label)
                self.assertIsNotNone(policy, "an undeclared datalink model")
                self.assertEqual(policy.identity, IDENTITY_REFUSE)

    def test_no_plaintext_credential_column_is_named_carelessly(self):
        # A column called `secret` or `token` that is neither a hash, nor a hint, nor an
        # explicitly-named presented key is probably a mistake. The two legitimate
        # plaintext columns are DatalinkPeer.api_key (the secret we present, the same
        # shape as OIDCProviderConfig.client_secret) and the magic tokens.
        allowed = {
            ("DatalinkGrant", "api_key_hash"), ("DatalinkGrant", "api_key_hint"),
            ("DatalinkGrant", "magic_token"),
            ("DatalinkPeer", "api_key"), ("DatalinkPeer", "magic_token"),
        }
        found = set()
        for model in apps.get_app_config("datalink").get_models():
            for field in model._meta.get_fields():
                if not getattr(field, "concrete", False):
                    continue
                if any(word in field.name for word in ("secret", "token", "api_key", "password")):
                    found.add((model._meta.object_name, field.name))
        self.assertEqual(found - allowed, set(), "an unexpected credential-shaped column")


class CredentialTests(TestCase):
    def test_a_key_is_returned_once_and_stored_only_as_a_hash(self):
        grant = DatalinkGrant.objects.create(label="peer")
        raw = grant.issue_api_key()
        grant.save()

        self.assertTrue(raw)
        self.assertNotIn(raw, grant.api_key_hash)
        self.assertTrue(grant.verify_api_key(raw))
        self.assertFalse(grant.verify_api_key(raw + "x"))
        self.assertFalse(grant.verify_api_key(""))

    def test_the_hint_identifies_a_key_without_revealing_it(self):
        grant = DatalinkGrant.objects.create(label="peer")
        raw = grant.issue_api_key()
        self.assertEqual(grant.api_key_hint, raw[-8:])
        self.assertLess(len(grant.api_key_hint), len(raw))

    def test_a_grant_with_no_key_verifies_nothing(self):
        # An unfinished pairing must not accept an empty key.
        grant = DatalinkGrant.objects.create(label="peer")
        self.assertFalse(grant.verify_api_key("anything"))

    def test_rotating_invalidates_the_previous_key(self):
        grant = DatalinkGrant.objects.create(label="peer")
        old = grant.issue_api_key()
        new = grant.issue_api_key()
        self.assertTrue(grant.verify_api_key(new))
        self.assertFalse(grant.verify_api_key(old))

    def test_a_magic_token_is_high_entropy_and_unique_by_default(self):
        a = DatalinkGrant.objects.create(label="a")
        b = DatalinkGrant.objects.create(label="b")
        self.assertNotEqual(a.magic_token, b.magic_token)
        self.assertGreaterEqual(len(a.magic_token), 32)

    def test_a_grant_expires_by_default(self):
        # It travels through a chat window during pairing, so it should not be
        # permanent unless somebody decides it is.
        grant = DatalinkGrant.objects.create(label="peer")
        self.assertIsNotNone(grant.expires_at)
        self.assertTrue(grant.can_be_read)

    def test_an_expired_grant_cannot_be_read(self):
        from django.utils import timezone

        grant = DatalinkGrant.objects.create(
            label="peer", expires_at=timezone.now() - timezone.timedelta(seconds=1),
        )
        self.assertTrue(grant.is_expired)
        self.assertFalse(grant.can_be_read)

    def test_a_deactivated_grant_cannot_be_read(self):
        grant = DatalinkGrant.objects.create(label="peer", is_active=False)
        self.assertFalse(grant.can_be_read)

    def test_scopes_default_to_granting_nothing(self):
        grant = DatalinkGrant.objects.create(label="peer")
        self.assertEqual(grant.scopes, [])
        self.assertFalse(grant.grants("people"))

    def test_a_grant_only_grants_what_it_lists(self):
        grant = DatalinkGrant.objects.create(label="peer", scopes=["people", "places"])
        self.assertTrue(grant.grants("people"))
        self.assertFalse(grant.grants("events"))


class RunShapeTests(TestCase):
    def _peer(self):
        return DatalinkPeer.objects.create(
            label="peer", base_url="http://peer.test", grant_uid=DatalinkGrant().grant_uid,
            magic_token="t", api_key="k",
        )

    def test_a_peer_cannot_be_deleted_while_a_run_records_what_it_sent(self):
        # PROTECT rather than CASCADE: revoking a peer must not erase the audit trail of
        # what was pulled from it. Revocation is is_active=False.
        from django.db.models import ProtectedError

        peer = self._peer()
        DatalinkRun.objects.create(peer=peer)
        with self.assertRaises(ProtectedError):
            peer.delete()

        peer.is_active = False
        peer.save(update_fields=["is_active"])
        self.assertFalse(DatalinkPeer.objects.get(pk=peer.pk).is_active)

    def test_a_null_total_is_the_indeterminate_bar(self):
        stage = DatalinkStageRun(run=DatalinkRun(peer=self._peer()), stage_key="people")
        self.assertIsNone(stage.percent)

        stage.total_rows, stage.rows_read = 200, 50
        self.assertEqual(stage.percent, 25)

    def test_percent_cannot_exceed_a_hundred_when_the_peer_grew_mid_run(self):
        stage = DatalinkStageRun(run=DatalinkRun(peer=self._peer()), stage_key="people",
                                 total_rows=10, rows_read=25)
        self.assertEqual(stage.percent, 100)

    def test_a_successful_stage_reads_as_complete(self):
        stage = DatalinkStageRun(run=DatalinkRun(peer=self._peer()), stage_key="people",
                                 status=DatalinkStageRun.STATUS_SUCCESS)
        self.assertEqual(stage.percent, 100)

    def test_awaiting_states_are_quiescent_but_not_terminal(self):
        # The poller stops on these — there is nothing to poll for until the operator
        # acts — but the run is not over and must not be reported as finished.
        for status in (DatalinkRun.STATUS_AWAITING_OPERATOR,
                       DatalinkRun.STATUS_AWAITING_CONFLICTS):
            with self.subTest(status=status):
                self.assertIn(status, DatalinkRun.QUIESCENT)
                self.assertNotIn(status, DatalinkRun.TERMINAL)

    def test_one_stage_row_per_stage_per_run(self):
        from django.db import IntegrityError, transaction

        run = DatalinkRun.objects.create(peer=self._peer())
        DatalinkStageRun.objects.create(run=run, stage_key="people")
        with self.assertRaises(IntegrityError), transaction.atomic():
            DatalinkStageRun.objects.create(run=run, stage_key="people")
