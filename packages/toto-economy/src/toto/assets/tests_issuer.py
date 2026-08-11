"""Monetary authority: a keypair, not a setting.

The property under test throughout is that authority cannot be *configured*
into existence. Everything fails closed — no row, no key, no secret, bad
ciphertext all answer "not the master".
"""

from io import StringIO

from cryptography.fernet import Fernet
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from toto.assets import currency_hash, issuer as issuer_module
from toto.assets.issuer import NotTheMaster, fingerprint_for
from toto.assets.models import CurrencyIssuer

ISSUER_SECRET = Fernet.generate_key().decode()
OTHER_SECRET = Fernet.generate_key().decode()


@override_settings(MONETARY_ISSUER_KEY=ISSUER_SECRET)
class MintingTests(TestCase):
    def test_minting_produces_a_usable_master(self):
        issuer = issuer_module.mint_issuer(label="Zenobia")

        self.assertTrue(issuer.is_self)
        self.assertTrue(issuer.public_key_pem.startswith("-----BEGIN PUBLIC KEY"))
        self.assertTrue(issuer.private_key_encrypted)
        self.assertTrue(issuer_module.is_monetary_master())

    def test_the_fingerprint_derives_from_the_public_half_alone(self):
        # It travels inside every genesis document, where a verifier has the
        # public key and nothing else.
        issuer = issuer_module.mint_issuer(label="Zenobia")
        self.assertEqual(issuer.fingerprint,
                         fingerprint_for(issuer.public_key_pem))

    def test_a_second_local_issuer_is_refused(self):
        issuer_module.mint_issuer(label="Zenobia")
        with self.assertRaises(NotTheMaster) as caught:
            issuer_module.mint_issuer(label="Impostor")
        self.assertIn("second monetary authority", str(caught.exception))

    def test_the_database_refuses_a_second_local_issuer_too(self):
        # Belt and braces: the service check above is convenience, the
        # constraint is the guarantee.
        issuer_module.mint_issuer(label="Zenobia")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CurrencyIssuer.objects.create(
                    label="Impostor", fingerprint="f" * 64,
                    public_key_pem="x", is_self=True)

    def test_minting_without_the_secret_is_refused_before_anything_is_written(self):
        with override_settings(MONETARY_ISSUER_KEY=""):
            with self.assertRaises(NotTheMaster):
                issuer_module.mint_issuer(label="Zenobia")
        self.assertEqual(CurrencyIssuer.objects.count(), 0)


class NotTheMasterTests(TestCase):
    """Every way a host can fail to be the master, and all of them fail closed."""

    @override_settings(MONETARY_ISSUER_KEY=ISSUER_SECRET)
    def test_no_issuer_row_at_all(self):
        self.assertFalse(issuer_module.is_monetary_master())

    @override_settings(MONETARY_ISSUER_KEY=ISSUER_SECRET)
    def test_a_pinned_remote_issuer_is_not_us(self):
        # What a BRANCH holds: the master's public key, to verify against.
        # Holding it lets you check a signature; it never lets you make one.
        CurrencyIssuer.objects.create(
            label="Zenobia", fingerprint="a" * 64,
            public_key_pem="-----BEGIN PUBLIC KEY-----", is_self=False)
        self.assertFalse(issuer_module.is_monetary_master())

    def test_the_key_material_without_the_secret_to_open_it(self):
        # The restored-backup case: assets rows travel in APPS_TO_SYNC, the
        # issuer secret deliberately does not. The clone holds ciphertext it
        # cannot open and must degrade to inert, not to master.
        with override_settings(MONETARY_ISSUER_KEY=ISSUER_SECRET):
            issuer_module.mint_issuer(label="Zenobia")
        with override_settings(MONETARY_ISSUER_KEY=""):
            self.assertFalse(issuer_module.is_monetary_master())

    def test_a_wrong_secret_does_not_open_the_key(self):
        with override_settings(MONETARY_ISSUER_KEY=ISSUER_SECRET):
            issuer_module.mint_issuer(label="Zenobia")
        with override_settings(MONETARY_ISSUER_KEY=OTHER_SECRET):
            self.assertFalse(issuer_module.is_monetary_master())

    @override_settings(MONETARY_ISSUER_KEY=ISSUER_SECRET)
    def test_corrupt_ciphertext_does_not_raise_out_of_the_predicate(self):
        issuer_module.mint_issuer(label="Zenobia")
        CurrencyIssuer.objects.filter(is_self=True).update(
            private_key_encrypted=b"not fernet at all")
        self.assertFalse(issuer_module.is_monetary_master())

    @override_settings(MONETARY_ISSUER_KEY=ISSUER_SECRET)
    def test_require_master_says_what_to_do_about_it(self):
        with self.assertRaises(NotTheMaster) as caught:
            issuer_module.require_master("issue assets")
        message = str(caught.exception)
        self.assertIn("issue assets", message)
        self.assertIn("signed mirrors", message)


@override_settings(MONETARY_ISSUER_KEY=ISSUER_SECRET)
class SigningTests(TestCase):
    def _document(self):
        issuer = issuer_module.local_issuer()
        return currency_hash.build_genesis(
            issuer_fingerprint=issuer.fingerprint, unit_name="ASR",
            name="Assarion", decimals=9,
            max_supply_base_units=6_666_666_666_667,
            issued_at="2026-08-11T12:00:00+00:00")

    def setUp(self):
        self.issuer = issuer_module.mint_issuer(label="Zenobia")

    def test_the_master_signs_and_anyone_verifies(self):
        document = self._document()
        signature = self.issuer.sign_genesis(document)
        self.assertTrue(self.issuer.verify_genesis(document, signature))

    def test_verification_needs_only_the_public_half(self):
        # The branch case, made explicit: strip the private key and the row can
        # still check a signature it did not make.
        document = self._document()
        signature = self.issuer.sign_genesis(document)

        pinned = CurrencyIssuer(label="Zenobia (pinned)",
                                fingerprint=self.issuer.fingerprint,
                                public_key_pem=self.issuer.public_key_pem,
                                is_self=False)
        self.assertTrue(pinned.verify_genesis(document, signature))
        with self.assertRaises(NotTheMaster):
            pinned.private_key()

    def test_a_tampered_document_does_not_verify(self):
        document = self._document()
        signature = self.issuer.sign_genesis(document)
        document["max_supply_base_units"] += 1
        self.assertFalse(self.issuer.verify_genesis(document, signature))


class CommandTests(TestCase):
    @override_settings(MONETARY_ISSUER_KEY=ISSUER_SECRET)
    def test_the_command_mints_and_reports_the_fingerprint(self):
        out = StringIO()
        call_command("mint_issuer_key", label="Zenobia", stdout=out)

        issuer = issuer_module.local_issuer()
        output = out.getvalue()
        self.assertIn(issuer.fingerprint, output)
        self.assertIn("MONETARY_ISSUER_KEY", output)
        self.assertIn("out of backups", output.lower())

    @override_settings(MONETARY_ISSUER_KEY=ISSUER_SECRET)
    def test_running_it_twice_is_a_command_error_not_a_traceback(self):
        call_command("mint_issuer_key", label="Zenobia", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("mint_issuer_key", label="Again", stdout=StringIO())

    @override_settings(MONETARY_ISSUER_KEY="")
    def test_without_the_secret_it_refuses_readably(self):
        with self.assertRaises(CommandError) as caught:
            call_command("mint_issuer_key", label="Zenobia", stdout=StringIO())
        self.assertIn("MONETARY_ISSUER_KEY", str(caught.exception))


class SecretSeparationTests(TestCase):
    """The single most important property in this module.

    Sealing the issuer key under FIELD_ENCRYPTION_KEY would make any restored
    production backup a second monetary master: assets rows travel in
    APPS_TO_SYNC and that key travels in the deploy config. Asserted
    behaviourally rather than by reading the source, so a fallback added
    anywhere in the chain fails this — not just one added to one function.
    """

    @override_settings(MONETARY_ISSUER_KEY="",
                       FIELD_ENCRYPTION_KEY=OTHER_SECRET)
    def test_a_host_with_only_the_field_key_cannot_mint(self):
        with self.assertRaises(NotTheMaster):
            issuer_module.mint_issuer(label="Impostor")
        self.assertEqual(CurrencyIssuer.objects.count(), 0)

    @override_settings(MONETARY_ISSUER_KEY=OTHER_SECRET,
                       FIELD_ENCRYPTION_KEY=ISSUER_SECRET)
    def test_a_key_sealed_under_the_issuer_secret_needs_that_secret(self):
        # Mint under one secret, then present the other as FIELD_ENCRYPTION_KEY
        # and confirm it buys nothing.
        issuer_module.mint_issuer(label="Zenobia")
        with override_settings(MONETARY_ISSUER_KEY="",
                               FIELD_ENCRYPTION_KEY=OTHER_SECRET):
            self.assertFalse(issuer_module.is_monetary_master())


class UnmigratedHostTests(TestCase):
    """The app installed and its tables not there yet — a real deploy state."""

    @override_settings(MONETARY_ISSUER_KEY=ISSUER_SECRET)
    def test_a_missing_table_means_not_the_master_rather_than_a_crash(self):
        from unittest import mock

        from django.db import DatabaseError

        with mock.patch("toto.assets.models.CurrencyIssuer.objects.filter",
                        side_effect=DatabaseError("no such table")):
            self.assertFalse(issuer_module.is_monetary_master())

    @override_settings(MONETARY_ISSUER_KEY=ISSUER_SECRET)
    def test_and_issuance_is_refused_readably_rather_than_500ing(self):
        from unittest import mock

        from django.db import DatabaseError

        with mock.patch("toto.assets.models.CurrencyIssuer.objects.filter",
                        side_effect=DatabaseError("no such table")):
            with self.assertRaises(NotTheMaster):
                issuer_module.require_master()
