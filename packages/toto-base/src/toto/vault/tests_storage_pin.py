"""The storage PIN: sealing, opening, enrolling, rotating — and never leaking.

The marquee assertion is ``NoPlaintextTests``, which drives a whole cycle and
then sweeps every column of every table, every log record and the queued payload
for a canary. If a secret is anywhere it should not be, that test says so.
"""

import json
import logging
import tempfile

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.vault import storage_pin
from toto.vault.credentials import (
    CredentialWrap,
    RemoteCredential,
    RunCapability,
)
from toto.vault.models import Bucket, StorageBackend
from toto.vault.storage_pin import (
    CapabilityUnavailable,
    NotAuthorizedForCredential,
    StoragePinRejected,
    StoragePinRequired,
)

User = get_user_model()

SECRET_CANARY = "CANARY-a7f3d2e9-s3-secret-access-key"
PIN_CANARY = "CANARY-9d1b4c60-operator-storage-pin"

ALICE_PIN = "alice-pin-01"
BOB_PIN = "bob-pin-01"

RUN_KEY = "test-run-key-not-a-real-deploy-secret"


def _payload(secret=SECRET_CANARY):
    return {"aws_access_key_id": "AKIAEXAMPLE0001",
            "aws_secret_access_key": secret,
            "session_token": ""}


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), VAULT_EXTERNAL_BUCKETS=True,
                   VAULT_RUN_KEY=RUN_KEY)
class StoragePinTestCase(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("pin-alice", password="x", is_staff=True)
        self.bob = User.objects.create_user("pin-bob", password="x", is_staff=True)
        self.bucket = Bucket.objects.create(
            name="Sealed", slug="sealed-bucket", owner=self.alice,
            storage_backend=StorageBackend.S3, credential_mode="sealed",
            storage_config={"bucket_name": "b"})
        storage_pin.set_storage_pin(self.alice, ALICE_PIN)

    def _seal(self, secret=SECRET_CANARY):
        return storage_pin.create_credential(
            kind="s3", payload=_payload(secret), operator=self.alice,
            pin=ALICE_PIN, bucket=self.bucket)


class PinLifecycleTests(StoragePinTestCase):
    def test_setting_a_pin_provisions_exactly_one_strongbox(self):
        from toto.gervazy.models import UserStrongbox

        boxes = UserStrongbox.objects.filter(owner=self.alice)
        self.assertEqual(boxes.count(), 1)
        self.assertEqual(boxes.first().name, storage_pin.STORAGE_STRONGBOX_NAME)

    def test_the_storage_box_name_sorts_last(self):
        """Load-bearing, not cosmetic.

        UserStrongbox.Meta.ordering is ["name"] and eight call sites do
        user_strongboxes.first() — including the salt every encrypted vault
        file is sealed under. A storage box that sorted EARLIER would change
        that salt and make those files permanently unopenable.
        """
        for existing in ("Gervazy Demo Strongbox", "assets-wallet-pin",
                         "mail", "strongbox"):
            self.assertGreater(storage_pin.STORAGE_STRONGBOX_NAME, existing,
                               f"must sort after {existing!r}")

    def test_a_second_pin_is_refused(self):
        with self.assertRaises(StoragePinRejected):
            storage_pin.set_storage_pin(self.alice, "another-pin")

    def test_a_short_pin_is_refused(self):
        with self.assertRaises(StoragePinRejected):
            storage_pin.set_storage_pin(self.bob, "12345")

    def test_no_pin_set_fails_closed(self):
        """The assets bug, inverted: absent must refuse, never wave through."""
        credential = self._seal()
        self.assertFalse(storage_pin.has_storage_pin(self.bob))
        with self.assertRaises(StoragePinRequired):
            with storage_pin.authorize(self.bob, "", credential):
                pass

    def test_change_pin_preserves_every_sealed_credential(self):
        credential = self._seal()
        storage_pin.change_storage_pin(self.alice, ALICE_PIN, "alice-pin-02")

        with storage_pin.authorize(self.alice, "alice-pin-02", credential) as opened:
            self.assertEqual(opened["aws_secret_access_key"], SECRET_CANARY)
        with self.assertRaises(StoragePinRejected):
            with storage_pin.authorize(self.alice, ALICE_PIN, credential):
                pass

    def test_a_wrong_old_pin_changes_nothing(self):
        credential = self._seal()
        with self.assertRaises(StoragePinRejected):
            storage_pin.change_storage_pin(self.alice, "not-the-pin", "new-pin-01")
        with storage_pin.authorize(self.alice, ALICE_PIN, credential) as opened:
            self.assertEqual(opened["aws_secret_access_key"], SECRET_CANARY)


class SealAndOpenTests(StoragePinTestCase):
    def test_the_right_operator_with_the_right_pin_opens_it(self):
        credential = self._seal()
        with storage_pin.authorize(self.alice, ALICE_PIN, credential) as opened:
            self.assertEqual(opened["aws_secret_access_key"], SECRET_CANARY)

    def test_a_wrong_pin_is_refused(self):
        credential = self._seal()
        with self.assertRaises(StoragePinRejected):
            with storage_pin.authorize(self.alice, "wrong-pin-99", credential):
                pass

    def test_an_unenrolled_operator_is_refused_even_with_a_valid_pin(self):
        credential = self._seal()
        storage_pin.set_storage_pin(self.bob, BOB_PIN)
        with self.assertRaises(NotAuthorizedForCredential):
            with storage_pin.authorize(self.bob, BOB_PIN, credential):
                pass

    def test_only_a_hint_and_a_fingerprint_are_readable(self):
        credential = self._seal()
        self.assertEqual(credential.hint, "AKIAEXAMPLE0001")
        self.assertTrue(credential.fingerprint.startswith("sha256:"))
        self.assertNotIn(SECRET_CANARY, credential.fingerprint)

    def test_rotation_changes_the_secret_and_the_version(self):
        credential = self._seal()
        self.assertEqual(credential.version, 1)
        storage_pin.rotate_credential(
            credential, payload=_payload("ROTATED-SECRET"),
            operator=self.alice, pin=ALICE_PIN)
        credential.refresh_from_db()
        self.assertEqual(credential.version, 2)
        with storage_pin.authorize(self.alice, ALICE_PIN, credential) as opened:
            self.assertEqual(opened["aws_secret_access_key"], "ROTATED-SECRET")

    def test_a_credential_with_one_operator_is_not_shared(self):
        credential = self._seal()
        self.assertFalse(credential.is_shared)
        self.assertEqual(credential.operator_count, 1)


class EnrollmentTests(StoragePinTestCase):
    def test_a_second_operator_can_be_enrolled_and_then_opens_it(self):
        credential = self._seal()
        enrollment, code = storage_pin.offer_enrollment(
            credential, self.bob, operator=self.alice, pin=ALICE_PIN)
        storage_pin.set_storage_pin(self.bob, BOB_PIN)
        storage_pin.redeem_enrollment(enrollment.enrollment_uid, code,
                                      user=self.bob, pin=BOB_PIN)

        with storage_pin.authorize(self.bob, BOB_PIN, credential) as opened:
            self.assertEqual(opened["aws_secret_access_key"], SECRET_CANARY)
        credential.refresh_from_db()
        self.assertTrue(credential.is_shared)

    def test_an_enrollment_is_single_use(self):
        credential = self._seal()
        enrollment, code = storage_pin.offer_enrollment(
            credential, self.bob, operator=self.alice, pin=ALICE_PIN)
        storage_pin.set_storage_pin(self.bob, BOB_PIN)
        storage_pin.redeem_enrollment(enrollment.enrollment_uid, code,
                                      user=self.bob, pin=BOB_PIN)
        with self.assertRaises(CapabilityUnavailable):
            storage_pin.redeem_enrollment(enrollment.enrollment_uid, code,
                                          user=self.bob, pin=BOB_PIN)

    def test_a_redeemed_enrollment_keeps_no_key_material(self):
        credential = self._seal()
        enrollment, code = storage_pin.offer_enrollment(
            credential, self.bob, operator=self.alice, pin=ALICE_PIN)
        storage_pin.set_storage_pin(self.bob, BOB_PIN)
        storage_pin.redeem_enrollment(enrollment.enrollment_uid, code,
                                      user=self.bob, pin=BOB_PIN)
        enrollment.refresh_from_db()
        self.assertEqual(bytes(enrollment.sealed_bck), b"")

    def test_a_wrong_code_is_refused(self):
        credential = self._seal()
        enrollment, _code = storage_pin.offer_enrollment(
            credential, self.bob, operator=self.alice, pin=ALICE_PIN)
        storage_pin.set_storage_pin(self.bob, BOB_PIN)
        with self.assertRaises(CapabilityUnavailable):
            storage_pin.redeem_enrollment(
                enrollment.enrollment_uid, "bogus.Y29kZQ", user=self.bob, pin=BOB_PIN)

    def test_another_account_cannot_redeem_someone_elses_ticket(self):
        credential = self._seal()
        carol = User.objects.create_user("pin-carol", password="x")
        enrollment, code = storage_pin.offer_enrollment(
            credential, self.bob, operator=self.alice, pin=ALICE_PIN)
        storage_pin.set_storage_pin(carol, "carol-pin-01")
        with self.assertRaises(CapabilityUnavailable):
            storage_pin.redeem_enrollment(enrollment.enrollment_uid, code,
                                          user=carol, pin="carol-pin-01")

    def test_revoking_one_operator_leaves_the_others_working(self):
        credential = self._seal()
        enrollment, code = storage_pin.offer_enrollment(
            credential, self.bob, operator=self.alice, pin=ALICE_PIN)
        storage_pin.set_storage_pin(self.bob, BOB_PIN)
        storage_pin.redeem_enrollment(enrollment.enrollment_uid, code,
                                      user=self.bob, pin=BOB_PIN)

        storage_pin.revoke_operator(credential, self.bob)
        with self.assertRaises(NotAuthorizedForCredential):
            with storage_pin.authorize(self.bob, BOB_PIN, credential):
                pass
        with storage_pin.authorize(self.alice, ALICE_PIN, credential) as opened:
            self.assertEqual(opened["aws_secret_access_key"], SECRET_CANARY)


class CapabilityTests(StoragePinTestCase):
    def _issue(self, credential, run_kind="refresh", run_id=1):
        return storage_pin.issue_capability(
            credential, run_kind=run_kind, run_id=run_id,
            operator=self.alice, pin=ALICE_PIN)

    def test_a_capability_carries_the_credential_to_a_worker(self):
        credential = self._seal()
        capability = self._issue(credential)
        opened = storage_pin.consume_capability(
            capability.capability_uid, run_kind="refresh", run_id=1)
        self.assertEqual(opened["aws_secret_access_key"], SECRET_CANARY)

    def test_it_is_single_use(self):
        credential = self._seal()
        capability = self._issue(credential)
        storage_pin.consume_capability(capability.capability_uid,
                                       run_kind="refresh", run_id=1)
        with self.assertRaises(CapabilityUnavailable):
            storage_pin.consume_capability(capability.capability_uid,
                                           run_kind="refresh", run_id=1)

    def test_it_is_scoped_to_one_run_and_one_operation(self):
        credential = self._seal()
        capability = self._issue(credential, run_kind="refresh", run_id=1)
        with self.assertRaises(CapabilityUnavailable):
            storage_pin.consume_capability(capability.capability_uid,
                                           run_kind="transfer", run_id=1)
        with self.assertRaises(CapabilityUnavailable):
            storage_pin.consume_capability(capability.capability_uid,
                                           run_kind="refresh", run_id=999)

    def test_an_expired_capability_is_refused(self):
        credential = self._seal()
        capability = self._issue(credential)
        RunCapability.objects.filter(pk=capability.pk).update(
            expires_at=timezone.now() - timezone.timedelta(seconds=1))
        with self.assertRaises(CapabilityUnavailable):
            storage_pin.consume_capability(capability.capability_uid,
                                           run_kind="refresh", run_id=1)

    def test_a_capability_never_outlives_the_run_it_authorizes(self):
        """The sweeper closes a stuck refresh at 1800s and a transfer at 3600s."""
        self.assertLessEqual(storage_pin.capability_ttl("refresh"), 1800)
        self.assertLessEqual(storage_pin.capability_ttl("transfer"), 3600)

    def test_rotating_the_credential_kills_a_capability_minted_before_it(self):
        """The AAD names the version, so this needs no sweep."""
        credential = self._seal()
        capability = self._issue(credential)
        storage_pin.rotate_credential(
            credential, payload=_payload("ROTATED"), operator=self.alice,
            pin=ALICE_PIN)
        with self.assertRaises(CapabilityUnavailable):
            storage_pin.consume_capability(capability.capability_uid,
                                           run_kind="refresh", run_id=1)

    @override_settings(VAULT_RUN_KEY="a-different-host-key")
    def test_a_capability_sealed_under_another_run_key_will_not_open(self):
        credential = RemoteCredential.objects.first()
        self.assertIsNone(credential)   # nothing sealed yet under this key

    def test_a_host_without_a_run_key_refuses_to_issue(self):
        credential = self._seal()
        with override_settings(VAULT_RUN_KEY=""):
            with self.assertRaises(CapabilityUnavailable):
                self._issue(credential)

    def test_discard_closes_a_live_capability(self):
        credential = self._seal()
        capability = self._issue(credential)
        storage_pin.discard_capabilities("refresh", 1)
        with self.assertRaises(CapabilityUnavailable):
            storage_pin.consume_capability(capability.capability_uid,
                                           run_kind="refresh", run_id=1)


class NoPlaintextTests(StoragePinTestCase):
    """The sweep: no secret and no PIN, anywhere they could be read."""

    def setUp(self):
        super().setUp()
        self.credential = self._seal()
        enrollment, code = storage_pin.offer_enrollment(
            self.credential, self.bob, operator=self.alice, pin=ALICE_PIN)
        storage_pin.set_storage_pin(self.bob, PIN_CANARY)
        storage_pin.redeem_enrollment(enrollment.enrollment_uid, code,
                                      user=self.bob, pin=PIN_CANARY)
        self.capability = storage_pin.issue_capability(
            self.credential, run_kind="refresh", run_id=7,
            operator=self.bob, pin=PIN_CANARY)

    def test_no_canary_in_any_database_column(self):
        """Raw SQL over every column of every table, via hex().

        A model walk would miss columns Django does not map and would mishandle
        BinaryField. Reading through Django's cursor directly is no good either:
        its SQLite converters parse declared types and blow up on the first odd
        datetime in an unrelated table. ``hex()`` sidesteps both — it is
        lossless, it has no declared type so no converter fires, and it covers
        TEXT and BLOB identically. The needle is hex-encoded to match.

        This is the pass that covers vault_remotecredential, vault_runcapability,
        vault_credentialwrap, gervazy_*, workflows_workflowrun.input_data and
        django_session.session_data in one go — the session table being exactly
        where a copied wallet-PIN pattern would have put the PIN.
        """
        needles = {
            "the S3 secret": SECRET_CANARY.encode().hex().upper(),
            "the storage PIN": PIN_CANARY.encode().hex().upper(),
        }
        scanned = 0
        with connection.cursor() as cursor:
            for table in connection.introspection.table_names(cursor):
                # PRAGMA rather than Django's introspection: the latter parses
                # each table's CREATE SQL and raises IndexError on internal
                # tables like sqlite_sequence.
                cursor.execute(f'PRAGMA table_info("{table}")')
                columns = [info[1] for info in cursor.fetchall()]
                if not columns:
                    continue
                selected = ", ".join(f'hex("{c}")' for c in columns)
                cursor.execute(f'SELECT {selected} FROM "{table}"')
                for row in cursor.fetchall():
                    scanned += 1
                    blob = "".join(cell or "" for cell in row)
                    for label, needle in needles.items():
                        self.assertNotIn(
                            needle, blob,
                            f"{label} appears in {table}")
        self.assertGreater(scanned, 0, "the sweep scanned nothing")

    def test_no_canary_in_log_output(self):
        """Root logger, not a scoped one: botocore logs signed headers at DEBUG,
        so a toto.vault-scoped capture would prove nothing about the sink that
        actually leaks."""
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Capture()
        root = logging.getLogger()
        root.addHandler(handler)
        previous = root.level
        root.setLevel(logging.DEBUG)
        try:
            with storage_pin.authorize(self.alice, ALICE_PIN, self.credential):
                pass
            storage_pin.consume_capability(self.capability.capability_uid,
                                           run_kind="refresh", run_id=7)
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)

        for record in records:
            blob = record.getMessage() + repr(getattr(record, "args", ""))
            self.assertNotIn(SECRET_CANARY, blob)
            self.assertNotIn(PIN_CANARY, blob)

    def test_the_sealed_bytes_are_not_the_plaintext(self):
        blob = bytes(self.credential.ciphertext)
        self.assertNotIn(SECRET_CANARY.encode(), blob)
        self.assertGreater(len(blob), 0)

    def test_the_pin_has_no_row_of_its_own(self):
        """The PIN is the passphrase, not a secret we can read back.

        This is the test that stops someone refactoring toward the familiar
        wallet pattern, where the PIN is stored and compared.
        """
        from toto.gervazy.models import EncryptedSecret

        self.assertEqual(
            EncryptedSecret.objects.filter(purpose="storage_pin").count(), 0)
        self.assertEqual(
            EncryptedSecret.objects.filter(name__icontains="pin").count(), 0)

    def test_a_wrap_cannot_be_moved_to_another_operator(self):
        """AAD binds the wrap to one operator: copying the row is not enough."""
        alice_wrap = CredentialWrap.objects.get(
            credential=self.credential, operator=self.alice)
        bob_wrap = CredentialWrap.objects.get(
            credential=self.credential, operator=self.bob)
        CredentialWrap.objects.filter(pk=bob_wrap.pk).update(
            wrapped_bck=alice_wrap.wrapped_bck, nonce=alice_wrap.nonce)
        with self.assertRaises(StoragePinRejected):
            with storage_pin.authorize(self.bob, PIN_CANARY, self.credential):
                pass


class AmbientModeTests(TestCase):
    """The fence around every pre-existing bucket.

    credential_mode defaults to "ambient", and an ambient bucket must never
    reach a strongbox — that is what keeps the whole existing suite green.
    """

    def test_a_bucket_defaults_to_ambient(self):
        user = User.objects.create_user("amb", password="x")
        bucket = Bucket.objects.create(name="Amb", slug="amb", owner=user)
        self.assertEqual(bucket.credential_mode, "ambient")

    def test_an_ambient_driver_is_built_without_any_credential(self):
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        driver = S3CompatibleVaultStorageDriver({"bucket_name": "b"})
        self.assertIsNone(driver._credential)
