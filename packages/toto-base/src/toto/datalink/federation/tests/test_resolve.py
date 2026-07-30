"""Resolving a page's identities and references — in bulk, and honestly.

The two properties worth proving:

* **a fixed number of queries per page, not per row.** The engine this replaces does one
  ``get()`` per foreign key per row on import and has no ``select_related`` at all on
  export. ``test_query_count_does_not_grow_with_the_page`` is the guard, and comparing
  two page sizes is the only form of performance test that does not flake.
* **an unresolvable reference stays unresolved.** It is absent from the result so the
  caller can report it, rather than being guessed at — guessing is what produces a row
  pointing at the wrong person.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from toto.datalink.models import DatalinkGrant, DatalinkIdentityMap, DatalinkPeer
from toto.datalink.registry import policy_for
from toto.datalink.services.canonical import identity_hash
from toto.datalink.services.resolve import (
    collect_references,
    identity_key,
    load_merge_bases,
    local_checksum,
    local_projection,
    resolve_identities,
    resolve_references,
)
from toto.datalink.services.serialize import serialize_row


def _peer():
    return DatalinkPeer.objects.create(
        label="peer", base_url="http://peer.test",
        grant_uid=DatalinkGrant().grant_uid, magic_token="t", api_key="k",
    )


class LocalProjectionTests(TestCase):
    def test_the_local_projection_is_the_same_function_as_the_emitter(self):
        # This is what makes the two checksums comparable at all. A second, parallel
        # projection that drifted would report phantom changes on every run forever.
        from toto.people.models import Person

        person = Person.objects.create(display_name="Ada", slug="ada")
        policy = policy_for("people.Person")
        self.assertEqual(local_projection(person, policy), serialize_row(person, policy))
        self.assertEqual(local_checksum(person, policy),
                         serialize_row(person, policy)["checksum"])

    def test_a_change_to_a_replicated_field_changes_the_local_checksum(self):
        from toto.people.models import Person

        person = Person.objects.create(display_name="Ada", slug="ada")
        policy = policy_for("people.Person")
        before = local_checksum(person, policy)
        person.bio = "changed"
        person.save(update_fields=["bio"])
        self.assertNotEqual(local_checksum(person, policy), before)

    def test_a_change_to_an_unreplicated_field_does_not(self):
        # Otherwise a column datalink does not even carry would manufacture conflicts.
        from toto.people.models import Person

        person = Person.objects.create(display_name="Ada", slug="ada")
        policy = policy_for("people.Person")
        before = local_checksum(person, policy)
        person.federated_sub = "sub-from-our-own-idp"
        person.save(update_fields=["federated_sub"])
        self.assertEqual(local_checksum(person, policy), before)

    def test_attaching_an_account_does_not_change_the_checksum(self):
        # A Person claimed by federation must not then look "locally edited" to the next
        # run — that would make every claimed profile a conflict.
        from toto.people.models import Person

        person = Person.objects.create(display_name="Ada", slug="ada")
        policy = policy_for("people.Person")
        before = local_checksum(person, policy)
        person.user = User.objects.create_user("ada")
        person.save(update_fields=["user"])
        self.assertEqual(local_checksum(person, policy), before)


class CollectReferenceTests(TestCase):
    def test_references_are_collected_from_fields_m2m_and_identities(self):
        rows = [{
            "identity": {"natural": {"bucket": {"__ref__": True, "model": "a.B", "uid": "1"}}},
            "fields": {"fk": {"__ref__": True, "model": "c.D", "uid": "2"}, "plain": 3},
            "m2m": {"tags": [{"__ref__": True, "model": "e.F", "uid": "3"}]},
        }]
        grouped = collect_references(rows)
        self.assertEqual(set(grouped), {"a.B", "c.D", "e.F"})

    def test_a_repeated_reference_is_collected_once(self):
        ref = {"__ref__": True, "model": "a.B", "uid": "1"}
        rows = [{"fields": {"x": dict(ref)}}, {"fields": {"y": dict(ref)}}]
        self.assertEqual(len(collect_references(rows)["a.B"]), 1)


class ResolveReferenceTests(TestCase):
    def test_a_uid_reference_resolves_to_the_local_row(self):
        from toto.locations.models import Address

        address = Address.objects.create(street="Main", country_name="X")
        ref = {"__ref__": True, "model": "locations.Address", "uid": str(address.uid)}
        resolved = resolve_references([{"fields": {"address": ref}}])
        self.assertEqual(
            resolved[identity_hash("locations.Address", {"uid": str(address.uid)})],
            address,
        )

    def test_a_reference_with_no_local_row_is_simply_absent(self):
        # Absent, never guessed. The caller turns this into a reported conflict.
        import uuid

        ref = {"__ref__": True, "model": "locations.Address", "uid": str(uuid.uuid4())}
        self.assertEqual(resolve_references([{"fields": {"a": ref}}]), {})

    def test_a_natural_key_reference_resolves(self):
        from toto.socialhub.models import Community, Constitution

        community = Community.objects.create(name="C", slug="c")
        Constitution.objects.create(community=community, title="T", slug="charter",
                                    body="b", version="1")
        ref = {"__ref__": True, "model": "socialhub.Constitution",
               "natural": {"slug": "charter"}}
        resolved = resolve_references([{"fields": {"c": ref}}])
        self.assertEqual(len(resolved), 1)

    def test_an_operator_approved_alias_redirects_a_reference(self):
        # Two instances that never synced hold different uids for the same row. Once an
        # operator approves the pairing, the peer's identity resolves to our row.
        import uuid

        from toto.locations.models import Address

        ours = Address.objects.create(street="Main", country_name="X")
        their_uid = str(uuid.uuid4())
        peer = _peer()
        DatalinkIdentityMap.objects.create(
            peer=peer, model_label="locations.Address",
            peer_identity={"uid": their_uid},
            peer_identity_hash=identity_hash("locations.Address", {"uid": their_uid}),
            local_identity={"uid": str(ours.uid)}, local_pk=str(ours.pk),
        )
        ref = {"__ref__": True, "model": "locations.Address", "uid": their_uid}
        resolved = resolve_references([{"fields": {"a": ref}}], peer=peer)
        self.assertEqual(resolved[identity_hash("locations.Address", {"uid": their_uid})], ours)

    def test_a_reference_to_a_refused_model_is_skipped_not_fatal(self):
        # The emitter would never send one, but a malformed payload from a peer must not
        # be able to take the receiver down.
        ref = {"__ref__": True, "model": "auth.User", "uid": "whatever"}
        self.assertEqual(resolve_references([{"fields": {"u": ref}}]), {})


class ResolveIdentityTests(TestCase):
    def test_an_existing_row_is_found_by_uid(self):
        from toto.people.models import Person

        person = Person.objects.create(display_name="Ada", slug="ada")
        policy = policy_for("people.Person")
        rows = [{"identity": {"uid": str(person.uid)}}]
        found = resolve_identities(policy, rows)
        self.assertEqual(found[identity_key("people.Person", rows[0]["identity"])], person)

    def test_an_absent_row_is_absent_from_the_result(self):
        import uuid

        policy = policy_for("people.Person")
        rows = [{"identity": {"uid": str(uuid.uuid4())}}]
        self.assertEqual(resolve_identities(policy, rows), {})

    def test_a_natural_key_identity_is_found(self):
        from toto.socialhub.models import Community, Constitution

        community = Community.objects.create(name="C", slug="c")
        Constitution.objects.create(community=community, title="T", slug="charter",
                                    body="b", version="1")
        policy = policy_for("socialhub.Constitution")
        rows = [{"identity": {"natural": {"slug": "charter"}}}]
        self.assertEqual(len(resolve_identities(policy, rows)), 1)


class QueryCountTests(TestCase):
    def test_query_count_does_not_grow_with_the_page(self):
        # The property that matters, and the only kind of performance assertion that
        # does not flake: resolving 20 rows must cost the same number of queries as
        # resolving 2. Per-row resolution is what makes the existing engine unusable at
        # size — and every one of these Persons has an `address` and a `patron` FK.
        from toto.locations.models import Address
        from toto.people.models import Person

        policy = policy_for("people.Person")

        def make(n, prefix):
            people = []
            for i in range(n):
                address = Address.objects.create(street=f"{prefix}{i}", country_name="X")
                people.append(Person.objects.create(
                    display_name=f"P{prefix}{i}", slug=f"p-{prefix}-{i}", address=address,
                ))
            return people

        small = make(2, "s")
        large = make(20, "l")

        def rows_for(people):
            return [serialize_row(p, policy) for p in people]

        small_rows, large_rows = rows_for(small), rows_for(large)

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx_small:
            refs = resolve_references(small_rows)
            resolve_identities(policy, small_rows, resolved_refs=refs)
        with CaptureQueriesContext(connection) as ctx_large:
            refs = resolve_references(large_rows)
            resolve_identities(policy, large_rows, resolved_refs=refs)

        self.assertEqual(
            len(ctx_small), len(ctx_large),
            f"query count grew with the page: {len(ctx_small)} for 2 rows vs "
            f"{len(ctx_large)} for 20 — resolution is per-row somewhere",
        )
        self.assertLessEqual(len(ctx_large), 8, "more queries per page than expected")


class MergeBaseTests(TestCase):
    def test_bases_load_in_one_query_keyed_by_identity(self):
        from toto.datalink.models import DatalinkMergeBase
        from toto.people.models import Person

        peer = _peer()
        policy = policy_for("people.Person")
        person = Person.objects.create(display_name="Ada", slug="ada")
        key = identity_key("people.Person", {"uid": str(person.uid)})
        DatalinkMergeBase.objects.create(
            peer=peer, model_label="people.Person", identity_hash=key,
            identity={"uid": str(person.uid)},
            peer_checksum="p", local_checksum="l", local_pk=str(person.pk),
        )
        with self.assertNumQueries(1):
            bases = load_merge_bases(peer, policy, [key])
        self.assertEqual(bases[key].peer_checksum, "p")
        self.assertEqual(bases[key].local_checksum, "l")

    def test_no_identities_means_no_query(self):
        peer = _peer()
        with self.assertNumQueries(0):
            self.assertEqual(load_merge_bases(peer, policy_for("people.Person"), []), {})
