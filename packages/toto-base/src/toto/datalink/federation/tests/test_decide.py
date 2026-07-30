"""The conflict decision table, exhaustively.

``decide`` is pure, so every row of the table can be asserted directly rather than
inferred from an end-to-end run. The cases that matter most are the ones where a
plausible simpler design silently loses data:

* both sides changed and there is no timestamp -> keep local and REPORT, never guess;
* a timestamp did arbitrate -> still record it, so no auto-resolution is invisible;
* the local row is gone -> that is a delete, not an absence, so do not resurrect it;
* nothing changed -> skip, and this must hold on the SECOND run of an unchanged link,
  which is where the single-checksum design fails.
"""
from dataclasses import dataclass

from django.test import SimpleTestCase
from django.utils import timezone

from toto.datalink.services.decide import (
    ADOPT,
    BOTH_CHANGED,
    CONFLICT,
    DELETED_LOCALLY,
    INSERT,
    NO_BASE_DIVERGENCE,
    REASON_IDENTICAL_NO_BASE,
    REASON_LOCAL_AHEAD,
    REASON_NEW,
    REASON_PEER_CHANGED,
    REASON_TIMESTAMP,
    REASON_UNCHANGED,
    SKIP,
    UPDATE,
    decide,
    field_diff,
)


@dataclass
class Base:
    """Only the two checksums are read, so the table can be driven directly."""

    peer_checksum: str
    local_checksum: str


class NoBaseTests(SimpleTestCase):
    def test_absent_locally_and_never_synced_is_an_insert(self):
        d = decide(local_present=False, base=None, peer_checksum="p", local_checksum=None)
        self.assertEqual((d.action, d.reason), (INSERT, REASON_NEW))

    def test_identical_and_never_synced_is_adopted_not_rewritten(self):
        # Recording a base without touching the row is what lets the NEXT run tell an
        # edit from a coincidence.
        d = decide(local_present=True, base=None, peer_checksum="same", local_checksum="same")
        self.assertEqual((d.action, d.reason), (ADOPT, REASON_IDENTICAL_NO_BASE))

    def test_different_and_never_synced_is_a_conflict_not_an_overwrite(self):
        # The first run between two populated instances SHOULD produce these. That is
        # correct, and it is the entire reason the feature is operator-gated.
        d = decide(local_present=True, base=None, peer_checksum="p", local_checksum="l",
                   local_fields={"name": "ours"}, peer_fields={"name": "theirs"})
        self.assertEqual(d.action, CONFLICT)
        self.assertEqual(d.conflict_kind, NO_BASE_DIVERGENCE)
        self.assertEqual(d.field_diff, {"name": {"local": "ours", "peer": "theirs"}})


class WithBaseTests(SimpleTestCase):
    def test_neither_side_changed_is_a_skip(self):
        d = decide(local_present=True, base=Base("p", "l"),
                   peer_checksum="p", local_checksum="l")
        self.assertEqual((d.action, d.reason), (SKIP, REASON_UNCHANGED))

    def test_only_the_peer_changed_is_an_update(self):
        d = decide(local_present=True, base=Base("old", "l"),
                   peer_checksum="new", local_checksum="l")
        self.assertEqual((d.action, d.reason), (UPDATE, REASON_PEER_CHANGED))

    def test_only_the_local_row_changed_is_a_skip_that_is_counted(self):
        # Not a conflict — nothing incoming would be lost — but it must be visible,
        # because this row will conflict as soon as the peer touches it.
        d = decide(local_present=True, base=Base("p", "old"),
                   peer_checksum="p", local_checksum="new")
        self.assertEqual((d.action, d.reason), (SKIP, REASON_LOCAL_AHEAD))

    def test_a_missing_local_row_is_a_delete_not_an_absence(self):
        d = decide(local_present=False, base=Base("p", "l"),
                   peer_checksum="p", local_checksum=None)
        self.assertEqual(d.action, CONFLICT)
        self.assertEqual(d.conflict_kind, DELETED_LOCALLY)
        self.assertIn("will not resurrect", d.detail)


class BothChangedTests(SimpleTestCase):
    def test_without_a_timestamp_the_local_row_is_kept_and_reported(self):
        # The no-silent-degradation line. Most of the replicated scope reaches here.
        d = decide(local_present=True, base=Base("op", "ol"),
                   peer_checksum="np", local_checksum="nl",
                   has_timestamp=False,
                   local_fields={"bio": "mine"}, peer_fields={"bio": "theirs"})
        self.assertEqual(d.action, CONFLICT)
        self.assertEqual(d.conflict_kind, BOTH_CHANGED)
        self.assertFalse(d.auto_resolved)
        self.assertIn("no modification timestamp", d.detail)
        self.assertEqual(d.field_diff, {"bio": {"local": "mine", "peer": "theirs"}})

    def test_a_later_peer_edit_wins_but_is_recorded(self):
        now = timezone.now()
        d = decide(local_present=True, base=Base("op", "ol"),
                   peer_checksum="np", local_checksum="nl",
                   has_timestamp=True,
                   peer_changed_at=now, local_changed_at=now - timezone.timedelta(hours=1))
        self.assertEqual((d.action, d.reason), (UPDATE, REASON_TIMESTAMP))
        # The auto-resolution is visible: a conflict row is still written, so the
        # operator sees "N auto-resolved by timestamp" rather than nothing at all.
        self.assertTrue(d.auto_resolved)
        self.assertEqual(d.conflict_kind, BOTH_CHANGED)

    def test_a_later_local_edit_wins_and_is_also_recorded(self):
        now = timezone.now()
        d = decide(local_present=True, base=Base("op", "ol"),
                   peer_checksum="np", local_checksum="nl",
                   has_timestamp=True,
                   peer_changed_at=now - timezone.timedelta(hours=1), local_changed_at=now)
        self.assertEqual((d.action, d.reason), (SKIP, REASON_TIMESTAMP))
        self.assertTrue(d.auto_resolved)

    def test_a_host_can_refuse_timestamp_tiebreaks_entirely(self):
        now = timezone.now()
        d = decide(local_present=True, base=Base("op", "ol"),
                   peer_checksum="np", local_checksum="nl",
                   has_timestamp=True, allow_timestamp_tiebreak=False,
                   peer_changed_at=now, local_changed_at=now - timezone.timedelta(hours=1))
        self.assertEqual(d.action, CONFLICT)

    def test_a_declared_timestamp_with_no_values_still_reports(self):
        # A model may declare a tiebreak and an individual row still have nulls; the
        # engine must not compare None and pick a winner by accident.
        d = decide(local_present=True, base=Base("op", "ol"),
                   peer_checksum="np", local_checksum="nl",
                   has_timestamp=True, peer_changed_at=None, local_changed_at=None)
        self.assertEqual(d.action, CONFLICT)
        self.assertFalse(d.auto_resolved)


class ChecksumPairTests(SimpleTestCase):
    """Why the base stores two checksums instead of one."""

    def test_a_save_that_transforms_the_row_does_not_read_as_a_local_edit(self):
        # The scenario: Address.save() re-derives lat/lon from geometry, so what landed
        # locally hashes differently from what the peer sent. The base records BOTH, so
        # on the next run — with the peer unchanged — this is a skip.
        base = Base(peer_checksum="what_peer_sent", local_checksum="what_landed_locally")
        d = decide(local_present=True, base=base,
                   peer_checksum="what_peer_sent", local_checksum="what_landed_locally")
        self.assertEqual((d.action, d.reason), (SKIP, REASON_UNCHANGED))

    def test_with_only_one_checksum_the_same_case_would_be_a_false_conflict(self):
        # Documents the bug the pair prevents: had the base stored the PEER checksum and
        # compared the local row against it, an untouched row would look locally edited
        # and — with the peer also having moved — become a conflict out of nothing.
        single = Base(peer_checksum="what_peer_sent", local_checksum="what_peer_sent")
        d = decide(local_present=True, base=single,
                   peer_checksum="peer_moved", local_checksum="what_landed_locally")
        self.assertEqual(d.action, CONFLICT, "this is what a single checksum would do")

        # With the pair, the same run is a clean update: only the peer moved.
        pair = Base(peer_checksum="what_peer_sent", local_checksum="what_landed_locally")
        d = decide(local_present=True, base=pair,
                   peer_checksum="peer_moved", local_checksum="what_landed_locally")
        self.assertEqual((d.action, d.reason), (UPDATE, REASON_PEER_CHANGED))


class FieldDiffTests(SimpleTestCase):
    def test_only_differing_fields_appear(self):
        diff = field_diff({"a": 1, "b": 2}, {"a": 1, "b": 3})
        self.assertEqual(diff, {"b": {"local": 2, "peer": 3}})

    def test_a_field_only_one_side_has_still_shows(self):
        # A field the peer dropped must be visible, not invisible.
        self.assertEqual(field_diff({"a": 1}, {}), {"a": {"local": 1, "peer": None}})
        self.assertEqual(field_diff({}, {"a": 1}), {"a": {"local": None, "peer": 1}})

    def test_nested_values_compare_by_content_not_by_key_order(self):
        self.assertEqual(field_diff({"m": {"x": 1, "y": 2}}, {"m": {"y": 2, "x": 1}}), {})

    def test_reference_dicts_compare_by_content(self):
        ref = {"__ref__": True, "model": "m", "uid": "u"}
        self.assertEqual(field_diff({"fk": ref}, {"fk": dict(ref)}), {})
        self.assertNotEqual(field_diff({"fk": ref}, {"fk": {**ref, "uid": "v"}}), {})


class SecondRunTests(SimpleTestCase):
    """The regression that would make a healthy link unusable.

    Applying the same unchanged page twice must produce nothing but skips. The realistic
    way this breaks is the single-checksum design: models whose ``save()`` transforms a
    row would every one of them report as locally edited on run 2. Parametrised over the
    models where that actually happens, named explicitly so the reason survives.
    """

    TRANSFORMING_MODELS = [
        # (label, why save() changes the row as it lands)
        ("locations.Address", "re-derives latitude/longitude from geometry"),
        ("people.Person", "generates a slug on collision"),
        ("socialhub.Community", "generates a slug on collision"),
        ("socialhub.Constitution", "generates a slug on collision"),
    ]

    def test_an_unchanged_row_is_skipped_on_every_subsequent_run(self):
        for label, why in self.TRANSFORMING_MODELS:
            with self.subTest(model=label, why=why):
                # Run 1 wrote the row; the base captured both sides as they really were.
                base = Base(peer_checksum="peer:v1", local_checksum="local:v1")
                # Run 2: the peer is unchanged, and re-projecting the local row gives
                # the same local checksum it gave last time.
                for _ in range(3):
                    d = decide(local_present=True, base=base,
                               peer_checksum="peer:v1", local_checksum="local:v1")
                    self.assertEqual(d.action, SKIP, f"{label} ({why})")
                    self.assertEqual(d.reason, REASON_UNCHANGED)
