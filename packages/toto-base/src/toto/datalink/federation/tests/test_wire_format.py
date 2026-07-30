"""The wire format: identities, references, checksums, pages, and what is left out.

Uses the `peer` database throughout, because that is the side rows are read *from* and
it keeps these tests honest about which alias they are touching.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from toto.datalink.registry import load_registry, policy_for
from toto.datalink.services.canonical import (
    canonical_json,
    identity_hash,
    ref_sort_key,
    row_checksum,
    sorted_refs,
)
from toto.datalink.services.payload import (
    DATALINK_SCHEMA_VERSION,
    BadCursor,
    build_manifest,
    decode_cursor,
    encode_cursor,
    serialize_page,
)
from toto.datalink.services.serialize import (
    DatalinkContractError,
    identity_of,
    reference_to,
    serialize_row,
)


class CanonicalTests(TestCase):
    def test_key_order_does_not_change_the_encoding(self):
        self.assertEqual(canonical_json({"b": 1, "a": 2}), canonical_json({"a": 2, "b": 1}))

    def test_identity_hash_is_scoped_by_model(self):
        # Constitution and MapLayer both key on a field called `slug`; without the model
        # label in the hash they would collide in the merge base.
        a = identity_hash("socialhub.Constitution", {"natural": {"slug": "charter"}})
        b = identity_hash("locations.MapLayer", {"natural": {"slug": "charter"}})
        self.assertNotEqual(a, b)

    def test_m2m_membership_is_part_of_the_checksum(self):
        # Otherwise adding someone to a community would be invisible to the engine.
        without = row_checksum({"name": "x"}, {})
        with_one = row_checksum({"name": "x"}, {"members": [{"uid": "1"}]})
        self.assertNotEqual(without, with_one)

    def test_m2m_order_does_not_change_the_checksum(self):
        # An unordered m2m would make the checksum flap between runs and manufacture
        # a conflict out of nothing.
        a = [{"__ref__": True, "model": "m", "uid": "b"},
             {"__ref__": True, "model": "m", "uid": "a"}]
        self.assertEqual(
            row_checksum({}, {"x": sorted_refs(a)}),
            row_checksum({}, {"x": sorted_refs(list(reversed(a)))}),
        )

    def test_natural_and_uid_references_both_sort(self):
        refs = [{"__ref__": True, "model": "m", "natural": {"slug": "b"}},
                {"__ref__": True, "model": "m", "uid": "a"}]
        self.assertEqual(len(sorted_refs(refs)), 2)
        self.assertEqual(sorted(ref_sort_key(r) for r in refs),
                         [ref_sort_key(r) for r in sorted_refs(refs)])


class CursorTests(TestCase):
    def test_a_cursor_round_trips(self):
        self.assertEqual(decode_cursor(encode_cursor(41)), "41")

    def test_no_cursor_means_the_beginning(self):
        self.assertIsNone(decode_cursor(None))
        self.assertIsNone(decode_cursor(""))

    def test_a_cursor_does_not_reveal_the_primary_key(self):
        # It is opaque so the receiver cannot come to depend on its shape, and so a
        # per-instance pk never appears in a payload the receiver stores.
        self.assertNotIn("41", encode_cursor(41))

    def test_a_malformed_cursor_raises_rather_than_restarting(self):
        # A resume that silently restarts on a bad cursor loops forever.
        with self.assertRaises(BadCursor):
            decode_cursor("not-base64-at-all!!")
        with self.assertRaises(BadCursor):
            decode_cursor(encode_cursor(1)[:-4])


class ReferenceTests(TestCase):
    databases = {"default", "peer"}

    def test_a_uid_model_references_by_uid(self):
        from toto.locations.models import Address

        address = Address.objects.create(street="Main", country_name="X")
        ref = reference_to(address)
        self.assertEqual(ref["model"], "locations.Address")
        self.assertEqual(ref["uid"], str(address.uid))
        self.assertNotIn("pk", ref)
        self.assertNotIn("id", ref)

    def test_a_natural_key_model_references_by_its_key(self):
        from toto.socialhub.models import Community, Constitution

        community = Community.objects.create(name="C", slug="c")
        constitution = Constitution.objects.create(
            community=community, title="Charter", slug="charter", body="b", version="1",
        )
        ref = reference_to(constitution)
        self.assertEqual(ref["natural"], {"slug": "charter"})

    def test_a_reference_to_a_refused_model_raises(self):
        # THE defect this replaces: backup_engine emits the raw integer pk here, which
        # names a different row on the receiver. auth.User is refused, so any attempt
        # to point at one must fail loudly.
        user = User.objects.create_user("ada")
        with self.assertRaises(DatalinkContractError) as caught:
            reference_to(user)
        self.assertIn("auth.User", str(caught.exception))
        self.assertIn("cross-instance identity", str(caught.exception))


class SerializeRowTests(TestCase):
    databases = {"default", "peer"}

    def _person(self, **kwargs):
        from toto.people.models import Person

        defaults = {"display_name": "Ada", "slug": "ada", "email": "ada@x.test"}
        return Person.objects.create(**{**defaults, **kwargs})

    def test_a_row_carries_its_identity_checksum_and_fields(self):
        person = self._person()
        row = serialize_row(person, policy_for("people.Person"))
        self.assertEqual(row["identity"], {"uid": str(person.uid)})
        self.assertEqual(len(row["checksum"]), 64)
        self.assertEqual(row["fields"]["display_name"], "Ada")
        self.assertEqual(row["fields"]["slug"], "ada")

    def test_no_primary_key_or_uid_appears_among_the_fields(self):
        person = self._person()
        row = serialize_row(person, policy_for("people.Person"))
        self.assertNotIn("id", row["fields"])
        self.assertNotIn("uid", row["fields"])

    def test_the_account_link_is_never_serialised(self):
        # The scope decision, at the level of a single row. Even for a Person that IS
        # attached to a local account, nothing about that account crosses.
        person = self._person(user=User.objects.create_user("ada2"))
        row = serialize_row(person, policy_for("people.Person"))
        self.assertNotIn("user", row["fields"])
        self.assertNotIn("federated_sub", row["fields"])
        blob = canonical_json(row)
        self.assertNotIn("ada2", blob)

    def test_a_dropped_reference_is_reported_as_omitted(self):
        # Community.email_service points at a refused model. The field does not travel,
        # and the row says so, so the receiver can count it rather than the operator
        # having to know.
        from toto.socialhub.models import Community

        community = Community.objects.create(name="C", slug="c")
        row = serialize_row(community, policy_for("socialhub.Community"))
        reasons = {entry["field"]: entry["reason"] for entry in row.get("omitted", [])}
        self.assertEqual(reasons.get("email_service"), "target_model_not_replicated")
        self.assertNotIn("email_service", row["fields"])

    def test_an_absent_geometry_column_is_reported_as_omitted(self):
        # This suite runs GIS-off, so locations has no geometry column at all — absent,
        # not null. The row records that rather than pretending a shape was sent.
        from toto.locations.models import Address

        address = Address.objects.create(street="Main", country_name="X")
        row = serialize_row(address, policy_for("locations.Address"))
        reasons = {entry["field"]: entry["reason"] for entry in row.get("omitted", [])}
        self.assertEqual(reasons.get("geometry"), "field_absent_on_this_build")

    def test_m2m_travels_as_sorted_references(self):
        from toto.socialhub.models import Community

        person = self._person()
        for slug in ("beta", "alpha"):
            person.communities.add(Community.objects.create(name=slug, slug=slug))
        row = serialize_row(person, policy_for("people.Person"))
        refs = row["m2m"]["communities"]
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs, sorted_refs(refs))

    def test_a_timestamped_model_carries_changed_at(self):
        from toto.socialhub.models import Community

        community = Community.objects.create(name="C", slug="c")
        row = serialize_row(community, policy_for("socialhub.Community"))
        self.assertIsNotNone(row["changed_at"])

    def test_a_model_without_a_timestamp_carries_none(self):
        row = serialize_row(self._person(), policy_for("people.Person"))
        self.assertNotIn("changed_at", row)

    def test_every_replicated_model_serialises(self):
        # A smoke net over the whole registry: a policy that names a field the
        # serializer cannot express should fail here, not on a customer's first run.
        from django.apps import apps

        for policy in load_registry().values():
            if policy.refused:
                continue
            model = apps.get_model(policy.model_label)
            obj = model._default_manager.first()
            if obj is None:
                continue
            with self.subTest(model=policy.model_label):
                serialize_row(obj, policy)


class ManifestTests(TestCase):
    databases = {"default", "peer"}

    def test_the_manifest_states_the_scope_invariants(self):
        # On the wire on purpose: a machine-checkable statement of the two decisions.
        manifest = build_manifest()
        self.assertIs(manifest["invariants"]["replicates_user_accounts"], False)
        self.assertIs(manifest["invariants"]["replicates_file_bytes"], False)

    def test_the_manifest_carries_the_schema_version_and_registry_digest(self):
        manifest = build_manifest()
        self.assertEqual(manifest["datalink"], DATALINK_SCHEMA_VERSION)
        self.assertEqual(len(manifest["registry_digest"]), 64)

    def test_an_ungranted_stage_is_listed_but_not_counted(self):
        # Listed so the UI can say "the peer did not grant this", rather than the stage
        # simply not existing and the operator wondering.
        manifest = build_manifest(granted_stages={"people"})
        by_key = {s["key"]: s for s in manifest["stages"]}
        self.assertTrue(by_key["people"]["granted"])
        self.assertFalse(by_key["events"]["granted"])
        self.assertIsNone(by_key["events"]["row_count"])

    def test_no_stage_advertises_a_refused_model(self):
        manifest = build_manifest()
        advertised = {m["model"] for s in manifest["stages"] for m in s["models"]}
        for label in ("auth.User", "vault.VaultFile", "socialhub.ConstitutionSignature"):
            self.assertNotIn(label, advertised)
        self.assertFalse(any(label.startswith("gervazy.") for label in advertised))

    def test_a_granted_stage_reports_a_row_count_for_the_progress_bar(self):
        from toto.locations.models import Address

        Address.objects.create(street="A", country_name="X")
        manifest = build_manifest()
        places = next(s for s in manifest["stages"] if s["key"] == "places")
        self.assertIsNotNone(places["row_count"])
        self.assertGreaterEqual(places["row_count"], 1)


class PageTests(TestCase):
    databases = {"default", "peer"}

    def _addresses(self, n):
        from toto.locations.models import Address

        return [Address.objects.create(street=f"S{i}", country_name="X") for i in range(n)]

    def test_a_page_stops_at_the_limit_and_offers_a_cursor(self):
        self._addresses(5)
        page = serialize_page(policy_for("locations.Address"), limit=2)
        self.assertEqual(page["count"], 2)
        self.assertTrue(page["has_more"])
        self.assertIsNotNone(page["next_cursor"])

    def test_the_cursor_walks_every_row_exactly_once(self):
        made = self._addresses(7)
        seen, cursor = [], None
        for _ in range(10):
            page = serialize_page(policy_for("locations.Address"), cursor=cursor, limit=3)
            seen += [r["identity"]["uid"] for r in page["rows"]]
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        self.assertEqual(sorted(seen), sorted(str(a.uid) for a in made))
        self.assertEqual(len(seen), len(set(seen)), "a row was served twice")

    def test_the_last_page_offers_no_cursor(self):
        self._addresses(2)
        page = serialize_page(policy_for("locations.Address"), limit=50)
        self.assertFalse(page["has_more"])
        self.assertIsNone(page["next_cursor"])

    def test_a_page_is_bounded_in_bytes_not_only_in_rows(self):
        # The transport truncates silently, so the server must never build a page it
        # would trim. A tiny budget must yield fewer rows AND a usable cursor.
        self._addresses(6)
        page = serialize_page(policy_for("locations.Address"), limit=6, max_bytes=1)
        self.assertEqual(page["count"], 1, "the budget must still emit one row")
        self.assertTrue(page["has_more"])
        self.assertIsNotNone(page["next_cursor"])

    def test_a_byte_bounded_walk_still_covers_everything(self):
        made = self._addresses(5)
        seen, cursor = [], None
        for _ in range(20):
            page = serialize_page(policy_for("locations.Address"), cursor=cursor,
                                  limit=5, max_bytes=1)
            seen += [r["identity"]["uid"] for r in page["rows"]]
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        self.assertEqual(sorted(seen), sorted(str(a.uid) for a in made))

    def test_the_page_limit_is_clamped(self):
        from toto.datalink.services.payload import MAX_PAGE_SIZE

        self._addresses(3)
        page = serialize_page(policy_for("locations.Address"), limit=10_000)
        self.assertLessEqual(page["count"], MAX_PAGE_SIZE)
