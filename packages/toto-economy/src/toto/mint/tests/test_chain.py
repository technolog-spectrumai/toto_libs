"""The monetary event chain: its hash, its signature, and its linkage.

Two layers. The arithmetic is pure and tested without a database; the walk
needs rows, so it gets them.
"""

from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase

from toto.assets.testing import LedgerTestCase as TestCase, make_asset
from toto.mint import chain, history
from toto.mint.chain import (EVENT_VERSION, GENESIS_PREV, MINT_CONTEXT,
                             MintEventError, build_event, compute_event_hash,
                             framed, verify_event)
from toto.mint.history import chain_head, head_hash, verify_chain
from toto.mint.models import CurrencyMintEvent
from toto.mint.services import append_event

HASH = "tcur1:" + "a" * 64


def _payload(**overrides):
    base = {
        "v": 1,
        "issuer_fingerprint": "a" * 64,
        "sequence": 0,
        "kind": "mint",
        "currency_hash": HASH,
        "amount_base_units": 1_000_000,
        "prev_hash": "",
        "issued_at": "2026-08-12T12:00:00+00:00",
    }
    base.update(overrides)
    return base


class ArithmeticTests(SimpleTestCase):
    def test_the_hash_is_prefixed_and_stable(self):
        digest = compute_event_hash(_payload())
        self.assertTrue(digest.startswith("tmev1:"))
        self.assertEqual(len(digest), 6 + 64)
        self.assertEqual(digest, compute_event_hash(_payload()))

    def test_every_field_moves_the_hash(self):
        baseline = compute_event_hash(_payload())
        variations = {
            "issuer_fingerprint": "c" * 64,
            "sequence": 1,
            "kind": "burn",
            "currency_hash": "tcur1:" + "b" * 64,
            "amount_base_units": 999,
            "prev_hash": "tmev1:" + "d" * 64,
            "issued_at": "2027-01-01T00:00:00+00:00",
        }
        for field, value in variations.items():
            with self.subTest(field=field):
                self.assertNotEqual(
                    baseline, compute_event_hash(_payload(**{field: value})))

    def test_an_extra_field_is_malformed_not_ignored(self):
        payload = _payload()
        payload["note"] = "hello"
        with self.assertRaises(MintEventError):
            compute_event_hash(payload)

    def test_only_two_verbs_move_supply(self):
        for bad in ("transfer", "distribute", "adjust", "MINT", ""):
            with self.subTest(kind=bad):
                with self.assertRaises(MintEventError):
                    compute_event_hash(_payload(kind=bad))

    def test_an_amount_is_always_positive(self):
        # A burn is kind=burn with a positive amount, never a negative mint.
        # One verb per act, so the history reads as what happened.
        for bad in (0, -1, 1.5, "100", True):
            with self.subTest(amount=bad):
                with self.assertRaises(MintEventError):
                    compute_event_hash(_payload(amount_base_units=bad))

    def test_a_currency_is_named_by_hash_not_by_ticker(self):
        with self.assertRaises(MintEventError):
            compute_event_hash(_payload(currency_hash="ASR"))

    def test_an_unknown_version_is_refused(self):
        with self.assertRaises(MintEventError):
            compute_event_hash(_payload(v=EVENT_VERSION + 1))

    def test_the_builder_stamps_the_version(self):
        payload = build_event(
            issuer_fingerprint="a" * 64, sequence=3, kind="burn",
            currency_hash=HASH, amount_base_units=5, prev_hash="tmev1:x",
            issued_at="2026-08-12T12:00:00+00:00")
        self.assertEqual(payload["v"], EVENT_VERSION)
        self.assertEqual(set(payload), set(chain.EVENT_FIELDS))


class ContextSeparationTests(SimpleTestCase):
    """A signature over one kind of thing must never verify as another."""

    def test_the_context_is_its_own(self):
        self.assertIn(b"/v1/", MINT_CONTEXT)
        from toto.assets.currency_hash import GENESIS_CONTEXT

        self.assertNotEqual(MINT_CONTEXT, GENESIS_CONTEXT)

    def test_a_mint_frame_is_never_a_genesis_or_statement_frame(self):
        from toto.assets import currency_hash as genesis
        from toto.assets import statement

        body = b"identical bytes"
        self.assertNotEqual(framed(body), genesis.framed(body))
        self.assertNotEqual(
            framed(body),
            statement.STATEMENT_CONTEXT + b"\x00"
            + len(body).to_bytes(4, "big") + body)

    def test_a_mint_frame_is_never_a_clearing_frame(self):
        from toto.clearing.services import wire

        body = b"identical bytes"
        for kind in wire.CONTEXTS:
            self.assertNotEqual(framed(body), wire.framed(kind, body), kind)


class SignatureTests(SimpleTestCase):
    def _keypair(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)

        private = Ed25519PrivateKey.generate()
        return private, private.public_key()

    def _pem(self, public):
        from cryptography.hazmat.primitives import serialization

        return public.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def test_a_signed_event_verifies(self):
        private, public = self._keypair()
        payload = _payload()
        signature = chain.sign_event(private, payload)
        self.assertTrue(verify_event(self._pem(public), payload, signature))

    def test_changing_the_amount_breaks_the_signature(self):
        private, public = self._keypair()
        payload = _payload()
        signature = chain.sign_event(private, payload)
        self.assertFalse(verify_event(
            self._pem(public), _payload(amount_base_units=1), signature))

    def test_a_genesis_signature_cannot_be_replayed_as_a_mint(self):
        # The two share a canonicalisation and a framing discipline, so the
        # context string is the only thing keeping them apart. Prove it does.
        from toto.assets import currency_hash as genesis

        private, public = self._keypair()
        payload = _payload()
        as_genesis = private.sign(genesis.framed(chain._canonical(payload)))
        self.assertFalse(verify_event(self._pem(public), payload,
                                      as_genesis.hex()))

    def test_garbage_inputs_return_false_rather_than_raising(self):
        _, public = self._keypair()
        self.assertFalse(verify_event("not a pem", _payload(), "zz"))
        self.assertFalse(verify_event(self._pem(public), _payload(), "nothex"))


class AppendTests(TestCase):
    def setUp(self):
        super().setUp()
        self.asset = make_asset(unit_name="ASR")

    def test_the_first_event_has_no_predecessor(self):
        self.assertIsNone(chain_head())
        self.assertEqual(head_hash(), GENESIS_PREV)

        event = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1000, reason="first")

        self.assertEqual(event.sequence, 0)
        self.assertEqual(event.prev_hash, GENESIS_PREV)
        self.assertTrue(event.event_hash.startswith("tmev1:"))

    def test_each_event_names_the_one_before_it(self):
        first = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1000, reason="first")
        second = append_event(asset=self.asset, kind="burn",
                              amount_base_units=400, reason="second")

        self.assertEqual(second.prev_hash, first.event_hash)
        self.assertEqual(second.sequence, 1)
        self.assertEqual(head_hash(), second.event_hash)

    def test_one_chain_spans_every_currency(self):
        # Per-currency supply is a filtered sum; the ORDER is platform-wide,
        # because there is one authority making these decisions.
        other = make_asset(unit_name="TPLN")
        first = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1000, reason="asr")
        second = append_event(asset=other, kind="mint",
                              amount_base_units=2000, reason="tpln")

        self.assertEqual(second.prev_hash, first.event_hash)
        self.assertEqual(
            list(CurrencyMintEvent.objects.values_list("sequence", flat=True)),
            [0, 1])

    def test_an_event_carries_its_own_provenance(self):
        event = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1000, reason="because")

        self.assertEqual(event.currency_hash, self.asset.currency_hash)
        self.assertEqual(event.payload["currency_hash"],
                         self.asset.currency_hash)
        self.assertTrue(event.verify())

    def test_a_reason_is_required(self):
        for bad in ("", "   "):
            with self.subTest(reason=bad):
                with self.assertRaises(ValidationError):
                    append_event(asset=self.asset, kind="mint",
                                 amount_base_units=1, reason=bad)

    def test_a_branch_cannot_append_anything(self):
        from toto.assets.issuer import NotTheMaster
        from toto.assets.models import CurrencyIssuer

        CurrencyIssuer.objects.filter(is_self=True).update(
            private_key_encrypted=None)

        with self.assertRaises(NotTheMaster):
            append_event(asset=self.asset, kind="mint",
                         amount_base_units=1000, reason="forge")
        self.assertEqual(CurrencyMintEvent.objects.count(), 0)

    def test_an_event_cannot_be_edited_or_deleted(self):
        event = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1000, reason="first")

        event.reason = "something else"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()

    def test_the_external_reference_is_reserved_and_empty(self):
        # Unused today; present so a future on-chain settlement can record
        # which remote fact each local event corresponds to.
        event = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1000, reason="first")
        self.assertEqual(event.external_ref, "")
        self.assertEqual(event.backend, "")


class SingleHeadTests(TestCase):
    """A chain is single-headed exactly when no two events share a parent.

    Every assertion here runs against sqlite, which is the point: this is the
    layer that holds without ``select_for_update``, and sqlite is what the
    clean-environment gate runs.
    """

    def setUp(self):
        super().setUp()
        self.asset = make_asset(unit_name="ASR")

    def _forge(self, *, prev_hash, marker):
        """Write a well-formed event by hand, bypassing append_event."""
        return CurrencyMintEvent.objects.create(
            sequence=900 + marker, kind="mint", asset=self.asset,
            currency_hash=self.asset.currency_hash, amount_base_units=1,
            prev_hash=prev_hash,
            event_hash="tmev1:" + str(marker) * 64,
            signature="00", issuer_fingerprint="a" * 64,
            payload={}, reason="forged")

    def test_two_events_cannot_share_a_parent(self):
        first = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1000, reason="first")
        second = append_event(asset=self.asset, kind="mint",
                              amount_base_units=1000, reason="second")
        self.assertEqual(second.prev_hash, first.event_hash)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._forge(prev_hash=first.event_hash, marker=1)

    def test_there_can_only_ever_be_one_root(self):
        # The root's parent is "", so the same constraint that forbids a fork
        # forbids a second beginning. No special case anywhere.
        append_event(asset=self.asset, kind="mint", amount_base_units=1000,
                     reason="first")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._forge(prev_hash=GENESIS_PREV, marker=2)

    def test_a_loser_lands_after_the_winner_not_beside_it(self):
        # The race, made deterministic: hand the appender a head that another
        # worker has already moved past. The insert is refused, and the retry
        # re-reads and appends after the winner.
        winner = append_event(asset=self.asset, kind="mint",
                              amount_base_units=1000, reason="winner")

        real_head = history.chain_head
        stale = [True]

        def racing(**kwargs):
            if stale:
                stale.pop()
                return None          # the world as it was before `winner`
            return real_head(**kwargs)

        with patch.object(history, "chain_head", racing):
            loser = append_event(asset=self.asset, kind="mint",
                                 amount_base_units=500, reason="loser")

        self.assertEqual(loser.prev_hash, winner.event_hash)
        self.assertEqual(loser.sequence, winner.sequence + 1)
        self.assertTrue(verify_chain(), verify_chain().findings)

    def test_a_refused_append_leaves_nothing_behind(self):
        winner = append_event(asset=self.asset, kind="mint",
                              amount_base_units=1000, reason="winner")

        def always_stale(**kwargs):
            return None

        with patch.object(history, "chain_head", always_stale):
            with self.assertRaises(IntegrityError):
                append_event(asset=self.asset, kind="mint",
                             amount_base_units=500, reason="hopeless")

        self.assertEqual(CurrencyMintEvent.objects.count(), 1)
        self.assertEqual(chain_head().pk, winner.pk)

    def test_the_sequence_is_the_head_plus_one_not_a_row_count(self):
        # A count would be a second, independent notion of "where we are",
        # and two notions can disagree. The head is the only one.
        for n in range(3):
            append_event(asset=self.asset, kind="mint",
                         amount_base_units=100, reason=f"m{n}")
        self.assertEqual(
            list(CurrencyMintEvent.objects.values_list("sequence", flat=True)),
            [0, 1, 2])


class VerifyChainTests(TestCase):
    def setUp(self):
        super().setUp()
        self.asset = make_asset(unit_name="ASR")

    def test_an_empty_chain_is_a_valid_chain(self):
        verdict = verify_chain()
        self.assertTrue(verdict)
        self.assertEqual(verdict.checked, 0)

    def test_a_written_chain_verifies(self):
        for n in range(3):
            append_event(asset=self.asset, kind="mint",
                         amount_base_units=1000, reason=f"mint {n}")

        verdict = verify_chain()
        self.assertTrue(verdict, verdict.findings)
        self.assertEqual(verdict.checked, 3)

    def test_altering_a_payload_is_caught(self):
        append_event(asset=self.asset, kind="mint", amount_base_units=1000,
                     reason="first")
        event = CurrencyMintEvent.objects.get()
        payload = dict(event.payload, amount_base_units=10 ** 9)
        CurrencyMintEvent.objects.filter(pk=event.pk).update(payload=payload)

        verdict = verify_chain()
        self.assertFalse(verdict)
        self.assertTrue(any("altered" in f for f in verdict.findings),
                        verdict.findings)

    def test_breaking_a_link_is_caught(self):
        append_event(asset=self.asset, kind="mint", amount_base_units=1000,
                     reason="first")
        second = append_event(asset=self.asset, kind="mint",
                              amount_base_units=1000, reason="second")
        CurrencyMintEvent.objects.filter(pk=second.pk).update(
            prev_hash="tmev1:" + "0" * 64)

        verdict = verify_chain()
        self.assertFalse(verdict)
        self.assertTrue(any("predecessor" in f for f in verdict.findings),
                        verdict.findings)

    def test_removing_an_event_from_the_middle_is_caught(self):
        # The property the chain exists for: history cannot be shortened
        # quietly, however the row was removed.
        first = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1000, reason="first")
        append_event(asset=self.asset, kind="mint", amount_base_units=1000,
                     reason="second")
        append_event(asset=self.asset, kind="mint", amount_base_units=1000,
                     reason="third")
        CurrencyMintEvent.objects.filter(pk=first.pk).delete()

        verdict = verify_chain()
        self.assertFalse(verdict)

    def test_a_signature_by_a_stranger_is_caught(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)

        append_event(asset=self.asset, kind="mint", amount_base_units=1000,
                     reason="first")
        event = CurrencyMintEvent.objects.get()
        stranger = Ed25519PrivateKey.generate()
        CurrencyMintEvent.objects.filter(pk=event.pk).update(
            signature=chain.sign_event(stranger, event.payload))

        verdict = verify_chain()
        self.assertFalse(verdict)
        self.assertTrue(any("does not verify" in f for f in verdict.findings),
                        verdict.findings)

    def test_every_finding_is_reported_not_just_the_first(self):
        # An operator repairing a compromised platform wants the whole list.
        for n in range(3):
            append_event(asset=self.asset, kind="mint",
                         amount_base_units=1000, reason=f"mint {n}")
        # Distinct bogus parents — the database will not hold two events
        # claiming the same one, which is Phase 5's guarantee doing its job.
        for n, event in enumerate(CurrencyMintEvent.objects.all()[1:]):
            CurrencyMintEvent.objects.filter(pk=event.pk).update(
                prev_hash=f"tmev1:{n}" + "0" * 63)

        self.assertGreaterEqual(len(verify_chain().findings), 2)
