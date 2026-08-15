"""The replication contract validates, and the validator actually bites.

Half of these assert the shipped registry is sound. The other half deliberately break
it and assert a specific complaint, because a validator nobody has seen fail is a
validator nobody should trust — and every rule here exists because of a real defect in
the backup engine this app replaces.

No database: everything is model introspection over the app registry.
"""
from django.test import SimpleTestCase, override_settings

from toto.datalink.registry import (
    CONFLICT_NEWEST,
    FK_NULL,
    IDENTITY_NATURAL,
    IDENTITY_REFUSE,
    IDENTITY_UID,
    STAGE_CONTENT,
    STAGE_EVENTS,
    STAGE_INFRA,
    STAGE_PEOPLE,
    STAGE_PLACES,
    STAGES,
    SyncPolicy,
    load_registry,
    registry_digest,
    replicated_policies,
    stage_models,
)
from toto.datalink.validate import describe_registry, validate_registry


def _mutate(**policies) -> dict:
    """The shipped registry with some policies replaced or added."""
    registry = load_registry()
    registry.update(policies)
    return registry


class ShippedRegistryTests(SimpleTestCase):
    def test_the_registry_validates_clean(self):
        problems = validate_registry()
        self.assertEqual(problems, [], "\n".join(problems))

    def test_every_model_of_every_claimed_app_is_accounted_for(self):
        # Rule 10. Silence must not mean "replicate it": a model added to one of these
        # apps next year fails here until somebody decides about it.
        problems = [p for p in validate_registry() if "no datalink policy" in p]
        self.assertEqual(problems, [])

    def test_something_is_actually_replicated(self):
        # A registry that refused everything would satisfy every other test here.
        replicated = replicated_policies()
        self.assertGreaterEqual(len(replicated), 15)
        self.assertEqual(
            {p.stage for p in replicated} - set(STAGES), set(),
            "a policy names a stage that is not in the run order",
        )

    def test_every_stage_has_at_least_one_model(self):
        # An empty stage would show as a step the operator can never advance past.
        for stage in STAGES:
            with self.subTest(stage=stage):
                if stage == "membership":
                    # Membership writes only m2m tables, declared on Person and
                    # Community, so it owns no models of its own by design.
                    continue
                self.assertTrue(stage_models(stage), f"stage {stage} writes nothing")


class NoAccountsTests(SimpleTestCase):
    """The decision that users are federation's job, asserted rather than assumed."""

    def test_auth_user_is_refused_with_a_reason(self):
        policy = load_registry()["auth.User"]
        self.assertEqual(policy.identity, IDENTITY_REFUSE)
        self.assertIn("federation", policy.refuse_reason.lower())

    def test_no_replicated_policy_emits_a_reference_to_a_user(self):
        # The structural version of the rule. Any FK to auth.User must be absent from
        # `fields` or declared FK_NULL; validate_registry enforces it, and this states
        # it in one place so the intent survives a refactor of the validator.
        from django.apps import apps

        offenders = []
        for policy in replicated_policies():
            model = apps.get_model(policy.model_label)
            for name in policy.fields:
                try:
                    field = model._meta.get_field(name)
                except Exception:
                    continue
                if not (field.is_relation and (field.many_to_one or field.one_to_one)):
                    continue
                if field.related_model._meta.label == "auth.User":
                    if policy.ref_policy(name) != FK_NULL:
                        offenders.append(f"{policy.model_label}.{name}")
        self.assertEqual(offenders, [], "these would emit an auth.User reference")

    def test_person_never_writes_its_account_link(self):
        # Stronger than FK_NULL, and the distinction matters: FK_NULL would emit the
        # field and write NULL, so every run would detach an already-claimed Person
        # from its account. Absent from `fields` means update_fields never names it.
        person = load_registry()["people.Person"]
        self.assertNotIn("user", person.fields)

    def test_person_never_carries_the_peers_federated_subject(self):
        # federated_sub is the RECEIVER's binding to its own identity provider.
        # Copying the peer's would make _link_person adopt the wrong Person.
        person = load_registry()["people.Person"]
        self.assertNotIn("federated_sub", person.fields)

    def test_person_slug_travels_so_federation_can_adopt_by_it(self):
        # _link_person falls back to adopting an unclaimed Person by slug, so the slug
        # is not cosmetic — it is the join key. It must travel, and a collision must be
        # a conflict rather than an auto-rename (asserted in the conflict tests).
        person = load_registry()["people.Person"]
        self.assertIn("slug", person.fields)
        self.assertIn(("slug",), person.unique_guards)

    def test_the_models_that_own_by_user_are_refused(self):
        registry = load_registry()
        for label in ("vault.Bucket", "vault.VaultFile", "vault.VaultDirectory",
                      "vault.FileGateway"):
            with self.subTest(label=label):
                self.assertEqual(registry[label].identity, IDENTITY_REFUSE)


class RefusalTests(SimpleTestCase):
    def test_gervazy_is_refused_wholesale(self):
        from django.apps import apps

        registry = load_registry()
        for model in apps.get_app_config("gervazy").get_models():
            with self.subTest(model=model._meta.label):
                self.assertEqual(registry[model._meta.label].identity, IDENTITY_REFUSE)

    def test_platform_is_refused_because_it_is_the_instances_identity(self):
        policy = load_registry()["core.Platform"]
        self.assertEqual(policy.identity, IDENTITY_REFUSE)

    def test_an_application_to_join_is_refused(self):
        """A refusal whose reason names why, not merely that. (This replaced
        the constitution-signature case when that model was retired; the
        discipline it pins is the same.)"""
        policy = load_registry()["socialhub.MembershipApplication"]
        self.assertEqual(policy.identity, IDENTITY_REFUSE)
        self.assertTrue(policy.refuse_reason.strip())

    def test_datalinks_own_credentials_are_refused(self):
        registry = load_registry()
        for label in ("datalink.DatalinkGrant", "datalink.DatalinkPeer"):
            with self.subTest(label=label):
                self.assertEqual(registry[label].identity, IDENTITY_REFUSE)

    def test_every_refusal_carries_a_reason(self):
        missing = [p.model_label for p in load_registry().values()
                   if p.refused and not p.refuse_reason]
        self.assertEqual(missing, [])


class OrderingTests(SimpleTestCase):
    """Intra-stage order is declaration order, and it has to satisfy the references."""

    def test_places_writes_dependencies_before_their_dependents(self):
        # The regression this catches: sorting a stage's models by label would put
        # Route before RouteChain and Territory before Address, writing rows before
        # the rows they point at. Alphabetical order is wrong here and looks right.
        order = [p.model_label for p in stage_models(STAGE_PLACES)]
        self.assertLess(order.index("locations.Address"), order.index("locations.Territory"))
        self.assertLess(order.index("locations.Address"), order.index("locations.Route"))
        self.assertLess(order.index("locations.Territory"), order.index("locations.Zone"))
        self.assertLess(order.index("locations.RouteChain"), order.index("locations.Route"))

    def test_events_writes_categories_and_events_before_invites(self):
        order = [p.model_label for p in stage_models(STAGE_EVENTS)]
        self.assertLess(order.index("events.EventCategory"),
                        order.index("events.ScheduledEvent"))
        self.assertLess(order.index("events.ScheduledEvent"),
                        order.index("events.EventInvite"))

    def test_content_writes_topics_before_the_posts_that_tag_them(self):
        order = [p.model_label for p in stage_models(STAGE_CONTENT)]
        self.assertLess(order.index("socialhub.CommunityNewsTopic"),
                        order.index("socialhub.CommunityNewsPost"))

    def test_the_person_community_cycle_is_broken_by_the_membership_stage(self):
        # Community.head -> Person is nullable and points backwards; Person has no FK
        # to Community, only the m2m. So no two-phase insert is needed anywhere —
        # people, then communities, then the m2m that closes the loop.
        registry = load_registry()
        person = registry["people.Person"]
        community = registry["socialhub.Community"]
        self.assertEqual(person.stage, STAGE_PEOPLE)
        self.assertEqual(person.effective_m2m_stage, "membership")
        self.assertEqual(community.effective_m2m_stage, "membership")
        self.assertLess(STAGES.index(person.stage), STAGES.index(community.stage))

    def test_self_references_are_declared_as_parent_fields(self):
        registry = load_registry()
        self.assertEqual(registry["people.Person"].parent_field, "patron")
        self.assertEqual(registry["socialhub.Community"].parent_field, "parent")


class TimestampTests(SimpleTestCase):
    def test_only_models_with_a_real_modification_stamp_declare_a_tiebreak(self):
        # The crux of the conflict design: most of the scope has no auto_now field, so
        # "newest wins" cannot be a timestamp comparison and the merge base has to do
        # the work. Anything claiming a tiebreak must have an auto_now field, which
        # validate_registry checks; here we pin down which models actually do.
        with_tiebreak = {p.model_label for p in replicated_policies() if p.timestamp_field}
        self.assertEqual(
            with_tiebreak,
            {"socialhub.Community", "socialhub.CommunityNewsPost"},
        )

    def test_the_models_without_one_are_reported_as_a_note(self):
        # Not an error — but it must be visible, or "no silent degradation" is a slogan.
        notes = " ".join(describe_registry())
        self.assertIn("no modification timestamp", notes)
        self.assertIn("people.Person", notes)


class DigestTests(SimpleTestCase):
    def test_the_digest_is_stable(self):
        self.assertEqual(registry_digest(), registry_digest())
        self.assertEqual(len(registry_digest()), 64)

    def test_prose_does_not_change_the_digest(self):
        # Two peers refuse to run when their digests differ, so a comment or a
        # refuse_reason edit must not make compatible instances reject each other.
        registry = load_registry()
        person = registry["people.Person"]
        chatty = SyncPolicy(**{**person.__dict__, "notes": "an entirely new explanation"})
        self.assertEqual(registry_digest(_mutate(**{"people.Person": chatty})),
                         registry_digest(registry))

    def test_changing_a_field_list_changes_the_digest(self):
        registry = load_registry()
        person = registry["people.Person"]
        trimmed = SyncPolicy(**{**person.__dict__,
                                "fields": tuple(f for f in person.fields if f != "bio")})
        self.assertNotEqual(registry_digest(_mutate(**{"people.Person": trimmed})),
                            registry_digest(registry))


class ValidatorBitesTests(SimpleTestCase):
    """Break the contract on purpose; assert the specific complaint."""

    def _problems(self, **policies) -> str:
        return "\n".join(validate_registry(_mutate(**policies)))

    def test_a_reference_to_an_unregistered_model_is_rejected(self):
        # backup_engine's actual behaviour here is to ship the raw integer pk, which
        # points at a different row on the receiver. Refusing at startup is the fix.
        registry = load_registry()
        without = {k: v for k, v in registry.items() if k != "locations.Address"}
        problems = "\n".join(validate_registry(without))
        self.assertIn("locations.Address", problems)
        self.assertIn("target has no policy", problems)

    def test_an_undeclared_reference_to_a_refused_model_is_rejected(self):
        # Person.user is the case that matters: putting it back in `fields` without
        # FK_NULL must fail, because auth.User is refused.
        person = load_registry()["people.Person"]
        careless = SyncPolicy(**{**person.__dict__,
                                 "fields": person.fields + ("user",),
                                 "refs": {}})
        problems = self._problems(**{"people.Person": careless})
        self.assertIn("people.Person.user -> auth.User", problems)
        self.assertIn("refused", problems)

    def test_a_non_nullable_reference_cannot_be_dropped(self):
        # EventInvite.event is NOT NULL, so declaring it FK_NULL is incoherent: the
        # right answer is to refuse EventInvite, and the message must say so.
        invite = load_registry()["events.EventInvite"]
        broken = SyncPolicy(**{**invite.__dict__, "refs": {"event": FK_NULL}})
        problems = self._problems(**{"events.EventInvite": broken})
        self.assertIn("events.EventInvite.event", problems)
        self.assertIn("NOT NULL", problems)

    def test_a_same_stage_target_declared_later_is_rejected(self):
        # Moving Address to the end of the places stage would break Territory.capital,
        # Route.start_address and Route.end_address. Declaration order IS the write
        # order, so the validator has to check position, not just stage.
        registry = load_registry()
        address = registry["locations.Address"]
        # Re-registering it last puts it at the end of the declaration order.
        mutated = dict(registry)
        del mutated["locations.Address"]
        mutated["locations.Address"] = address
        from toto.datalink import registry as registry_module

        original = dict(registry_module._ORDER)
        try:
            registry_module._ORDER["locations.Address"] = max(original.values()) + 1
            problems = "\n".join(validate_registry(mutated))
        finally:
            registry_module._ORDER.clear()
            registry_module._ORDER.update(original)
        self.assertIn("declared AFTER", problems)

    def test_a_target_in_a_later_stage_is_rejected(self):
        person = load_registry()["people.Person"]
        too_early = SyncPolicy(**{**person.__dict__, "stage": STAGE_INFRA})
        problems = self._problems(**{"people.Person": too_early})
        self.assertIn("later stage", problems)

    def test_a_natural_key_with_no_unique_constraint_is_rejected(self):
        community = load_registry()["socialhub.Community"]
        unsound = SyncPolicy(**{**community.__dict__,
                                "identity": IDENTITY_NATURAL,
                                "natural_key": ("established_year",)})
        problems = self._problems(**{"socialhub.Community": unsound})
        self.assertIn("not backed by a unique constraint", problems)

    def test_a_uid_identity_without_a_uid_field_is_rejected(self):
        # Station is a plain model — no DomainEntity, so no uid to key on.
        station = load_registry()["socialhub.Station"]
        wrong = SyncPolicy(**{**station.__dict__,
                              "identity": IDENTITY_UID, "natural_key": ()})
        problems = self._problems(**{"socialhub.Station": wrong})
        self.assertIn("no unique field named uid", problems)

    def test_an_auto_now_field_cannot_be_replicated(self):
        community = load_registry()["socialhub.Community"]
        wrong = SyncPolicy(**{**community.__dict__,
                              "fields": community.fields + ("updated_at",)})
        problems = self._problems(**{"socialhub.Community": wrong})
        self.assertIn("auto_now", problems)

    def test_a_creation_stamp_cannot_serve_as_a_tiebreak(self):
        # auto_now_add records creation, not modification, so using it to pick a winner
        # would be choosing at random while looking principled.
        post = load_registry()["socialhub.CommunityNewsPost"]
        wrong = SyncPolicy(**{**post.__dict__, "timestamp_field": "created_at"})
        problems = self._problems(**{"socialhub.CommunityNewsPost": wrong})
        self.assertIn("modification stamp", problems)

    def test_a_primary_key_cannot_be_replicated(self):
        event = load_registry()["events.ScheduledEvent"]
        wrong = SyncPolicy(**{**event.__dict__, "fields": event.fields + ("id",)})
        problems = self._problems(**{"events.ScheduledEvent": wrong})
        self.assertIn("per-instance", problems)

    def test_a_side_effecting_model_must_stay_refused(self):
        request = load_registry()["socialhub.ReferenceRequest"]
        revived = SyncPolicy(**{**request.__dict__,
                                "identity": IDENTITY_UID,
                                "refuse_reason": "",
                                "fields": ("status",)})
        problems = self._problems(**{"socialhub.ReferenceRequest": revived})
        self.assertIn("side effects", problems)

    def test_a_missing_policy_is_rejected(self):
        registry = load_registry()
        without = {k: v for k, v in registry.items() if k != "events.Availability"}
        problems = "\n".join(validate_registry(without))
        self.assertIn("events.Availability", problems)
        self.assertIn("no datalink policy", problems)

    def test_an_m2m_in_the_fields_list_is_rejected(self):
        person = load_registry()["people.Person"]
        wrong = SyncPolicy(**{**person.__dict__,
                              "fields": person.fields + ("communities",)})
        problems = self._problems(**{"people.Person": wrong})
        self.assertIn("many-to-many belongs in m2m", problems)

    def test_a_parent_field_must_be_a_nullable_self_reference(self):
        person = load_registry()["people.Person"]
        wrong = SyncPolicy(**{**person.__dict__, "parent_field": "address"})
        problems = self._problems(**{"people.Person": wrong})
        self.assertIn("must point at people.Person", problems)

    def test_an_m2m_to_a_refused_model_is_rejected(self):
        # The shape that would silently drop a permission-bearing whitelist.
        event = load_registry()["events.ScheduledEvent"]
        registry = _mutate(**{"events.ScheduledEvent": event})
        registry["people.Person"] = SyncPolicy(
            "people.Person", stage=STAGE_PEOPLE, identity=IDENTITY_REFUSE,
            refuse_reason="pretend",
        )
        problems = "\n".join(validate_registry(registry))
        self.assertIn("events.ScheduledEvent.organizers", problems)
        self.assertIn("refused", problems)


class UninstalledAppTests(SimpleTestCase):
    def test_a_policy_for_an_app_this_host_lacks_is_not_an_error(self):
        # The failure mode APPS_TO_SYNC has and this must not: one stale label there
        # makes backup_engine.iter_models() raise LookupError and breaks every backup
        # and restore path. A policy for an absent app is simply skipped.
        registry = _mutate(**{
            "kanban.Project": SyncPolicy(
                "kanban.Project", stage=STAGE_INFRA, identity=IDENTITY_UID,
                fields=("name",),
            )
        })
        self.assertEqual(validate_registry(registry), [])


class ConflictDefaultTests(SimpleTestCase):
    def test_every_replicated_policy_uses_the_merge_base(self):
        # insert_only exists in the vocabulary but nothing in the current scope needs
        # it; if something starts to, this test is the place to say why.
        modes = {p.conflict for p in replicated_policies()}
        self.assertEqual(modes, {CONFLICT_NEWEST})
