"""The serialization contract. If these change, every existing chain breaks."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.test import SimpleTestCase

from toto.ledger.canonical import (
    ALGORITHMS,
    DEFAULT_ALGORITHM,
    FORMAT_VERSION,
    CanonicalError,
    UnknownAlgorithm,
    canonical_payload,
    canonicalize,
    digest,
    resolve,
)


class DeterminismTests(SimpleTestCase):
    def test_map_key_order_does_not_change_the_bytes(self):
        one = canonical_payload({"b": 2, "a": 1, "c": 3})
        two = canonical_payload({"c": 3, "a": 1, "b": 2})
        self.assertEqual(one, two)

    def test_the_same_structure_serializes_identically_every_time(self):
        value = {"title": "Résumé & <tag>", "units": Decimal("0.000001"),
                 "rows": [1, True, None]}
        self.assertEqual(canonical_payload(value), canonical_payload(value))

    def test_list_order_is_preserved(self):
        self.assertNotEqual(canonical_payload([1, 2]), canonical_payload([2, 1]))


class InjectivityTests(SimpleTestCase):
    """No two different structures may produce the same text."""

    def test_true_and_one_are_different_preimages(self):
        self.assertNotEqual(canonical_payload(True), canonical_payload(1))

    def test_a_number_and_its_string_are_different_preimages(self):
        self.assertNotEqual(canonical_payload(1), canonical_payload("1"))

    def test_decimal_and_string_are_different_preimages(self):
        self.assertNotEqual(
            canonical_payload(Decimal("1.5")), canonical_payload("1.5"),
        )

    def test_none_and_empty_string_are_different_preimages(self):
        self.assertNotEqual(canonical_payload(None), canonical_payload(""))

    def test_a_boundary_cannot_slide_between_two_fields(self):
        """The collision netstrings exist to prevent, prevented by tags."""
        self.assertNotEqual(
            canonical_payload({"a": "xy", "b": "z"}),
            canonical_payload({"a": "x", "b": "yz"}),
        )

    def test_a_list_of_one_is_not_the_bare_value(self):
        self.assertNotEqual(canonical_payload(["x"]), canonical_payload("x"))


class ExactnessTests(SimpleTestCase):
    def test_a_decimal_keeps_every_digit(self):
        self.assertIn("0.000001", canonical_payload(Decimal("0.000001")))

    def test_a_float_is_refused_rather_than_rounded(self):
        with self.assertRaises(CanonicalError):
            canonical_payload(0.1)

    def test_a_non_string_key_is_refused(self):
        with self.assertRaises(CanonicalError):
            canonical_payload({1: "one"})

    def test_dates_and_datetimes_are_tagged_apart(self):
        day = dt.date(2026, 8, 25)
        moment = dt.datetime(2026, 8, 25, 0, 0)
        self.assertNotEqual(canonical_payload(day), canonical_payload(moment))


class EscapingTests(SimpleTestCase):
    def test_markup_in_a_value_cannot_break_out(self):
        text = canonical_payload({"note": "</text></entry><entry key='x'>"})
        # Round-tripping proves the payload still parses as one entry.
        self.assertEqual(canonicalize(text), text)

    def test_a_quote_in_a_key_is_escaped(self):
        text = canonical_payload({'a"b': 1})
        self.assertIn("&quot;", text)
        self.assertEqual(canonicalize(text), text)


class BudgetTests(SimpleTestCase):
    def test_a_payload_deeper_than_the_budget_is_refused(self):
        value = "leaf"
        for _ in range(40):
            value = {"next": value}
        with self.assertRaises(CanonicalError):
            canonical_payload(value)

    def test_a_payload_wider_than_the_budget_is_refused(self):
        with self.assertRaises(CanonicalError):
            canonical_payload(list(range(6000)))


class CanonicalizeTests(SimpleTestCase):
    def test_attribute_order_is_normalised(self):
        self.assertEqual(
            canonicalize('<x b="2" a="1"/>'), canonicalize('<x a="1" b="2"/>'),
        )

    def test_insignificant_whitespace_is_dropped(self):
        self.assertEqual(
            canonicalize("<x>  <y>  v  </y>  </x>"), canonicalize("<x><y>v</y></x>"),
        )

    def test_a_doctype_is_refused(self):
        with self.assertRaises(CanonicalError):
            canonicalize('<!DOCTYPE x [<!ENTITY e "v">]><x>&e;</x>')

    def test_an_entity_declaration_is_refused(self):
        with self.assertRaises(CanonicalError):
            canonicalize('<!ENTITY e SYSTEM "file:///etc/passwd"><x/>')

    def test_malformed_xml_is_refused(self):
        with self.assertRaises(CanonicalError):
            canonicalize("<x><y></x>")

    def test_canonicalizing_is_idempotent(self):
        once = canonicalize('<a z="1"><b>  t  </b></a>')
        self.assertEqual(canonicalize(once), once)


class AlgorithmRegistryTests(SimpleTestCase):
    def test_the_default_is_sha256_for_interoperability(self):
        self.assertEqual(DEFAULT_ALGORITHM, "sha256")

    def test_an_unknown_algorithm_refuses_rather_than_guessing(self):
        with self.assertRaises(UnknownAlgorithm):
            resolve("md5-ish")

    def test_the_refusal_names_what_it_does_know(self):
        with self.assertRaises(UnknownAlgorithm) as caught:
            digest("x", algorithm="nope")
        self.assertIn("sha256", str(caught.exception))

    def test_every_registered_algorithm_actually_hashes(self):
        for name in ALGORITHMS:
            with self.subTest(algorithm=name):
                self.assertTrue(digest("x", algorithm=name))

    def test_different_algorithms_give_different_digests(self):
        self.assertNotEqual(
            digest("x", algorithm="sha256"), digest("x", algorithm="sha512"),
        )


class KnownVectorTests(SimpleTestCase):
    """Pin the bytes, not just the behaviour.

    These are the tests that fail when somebody 'tidies' the serializer. That
    is their whole job: a chain sealed last year has to keep verifying, and the
    only way to notice a silent format change is to write the answer down.
    """

    def test_the_format_version_is_what_chains_record(self):
        self.assertEqual(FORMAT_VERSION, "bc-ledger-xml-1")

    def test_a_known_payload_has_a_known_shape(self):
        self.assertEqual(
            canonical_payload({"a": 1, "b": "x"}),
            '<payload><map><entry key="a"><int>1</int></entry>'
            '<entry key="b"><text>x</text></entry></map></payload>',
        )

    def test_a_known_payload_has_a_known_sha256(self):
        self.assertEqual(
            digest(canonical_payload({"a": 1}), algorithm="sha256"),
            digest(
                '<payload><map><entry key="a"><int>1</int></entry></map></payload>',
                algorithm="sha256",
            ),
        )

    def test_the_empty_map_is_stable(self):
        self.assertEqual(canonical_payload({}), "<payload><map></map></payload>")


class TheTwoDoorsAgreeTests(SimpleTestCase):
    """`append()` takes either a structure or XML text, and canonicalizes text.

    So `canonicalize(canonical_payload(x))` MUST equal `canonical_payload(x)`
    for every x. When it does not, the same content hashes differently
    depending on which argument the caller used — and a chain written through
    one door stops verifying through the other. This caught exactly that: text
    escaping used `quote=True` on one side and `quote=False` on the other.
    """

    HOSTILE = [
        {"note": "</text></entry><entry key='x'>"},
        {"quote": 'he said "no"'},
        {"apostrophe": "it's fine"},
        {"amp": "a & b"},
        {"mixed": "<a href='x'>&amp;</a>"},
        {"unicode": "Zażółć gęślą jaźń — ✓"},
        {"nested": {"in'ner": ['q"uote', "amp&"]}},
        "bare 'string' with \"both\"",
        ["<x/>", {"k": "v'"}],
    ]

    def test_a_built_payload_survives_recanonicalization_unchanged(self):
        for value in self.HOSTILE:
            with self.subTest(value=value):
                built = canonical_payload(value)
                self.assertEqual(canonicalize(built), built)

    def test_both_doors_reach_the_same_digest(self):
        for value in self.HOSTILE:
            with self.subTest(value=value):
                built = canonical_payload(value)
                self.assertEqual(
                    digest(built, algorithm="sha256"),
                    digest(canonicalize(built), algorithm="sha256"),
                )
