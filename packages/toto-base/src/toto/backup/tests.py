"""References to models that may not be backed up, and why they need care.

## The defect these pin

``BackupEngine.serialize_object`` exports a foreign key to a model **without** a
``uid`` as the raw ``*_id`` integer, and a restore renumbers primary keys. So a
reference into such a model came back attached to whichever unrelated row now
held that number — a company's statute becoming somebody else's file, silently,
with no error at restore time and nothing wrong-looking in the database.

Five models in zenobia's backup set point into ``vault``: ``socialhub.Community.
statute``, kanban's ``DocumentationPage.vault_file`` and ``MissionAttachment.
vault_file``, and governance's ``UmowaAmendment.document`` and
``Mandate.document``.

## Why the obvious fix is forbidden

Giving ``VaultFile`` a ``uid`` is exactly what ``vault/apps.py`` refuses at
startup, and the reason is good: ``is_backup_model()`` selects models for the
archive purely by the presence of a field with that name, so a ``uid`` anywhere
in vault would make bucket-peering credentials — grant tokens, peer API keys —
eligible for a signed, pullable backup. ``tests_peering.
test_no_vault_model_has_a_uid_field`` asserts it independently.

## What replaces it

``BACKUP_NATURAL_REF``: a tuple of ORM lookups naming a model's own stable
identity. For ``VaultFile`` that is ``(bucket.slug, key)`` — already unique, and
already how the system identifies a file across hosts, since ``mirror.py``
places a file on a peer with exactly ``filter(bucket=..., key=...)``. So the
reference does not merely survive as an identifier: on a host that has mirrored
the bucket it **resolves to the right file**.

Where it cannot resolve, the field drops to NULL with a warning naming what was
lost, and a row whose REQUIRED reference cannot resolve is skipped rather than
aborting the whole restore — a distinction the previous code did not draw, and
which would have turned this improvement into a restore-killer.
"""

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.backup.services.backup_engine import BackupEngine
from toto.backup.services.sync_service import SyncService, UnresolvableReference
from toto.socialhub.models import Community
from toto.vault.models import Bucket, VaultFile


def _owner():
    return get_user_model().objects.get_or_create(username="owner")[0]


def _file(key="statute", bucket_slug="docs"):
    owner = _owner()
    bucket, _ = Bucket.objects.get_or_create(
        slug=bucket_slug, defaults={"name": bucket_slug, "owner": owner})
    return VaultFile.objects.create(bucket=bucket, key=key, owner=owner)


class NaturalReferenceTests(TestCase):

    def setUp(self):
        self.engine = BackupEngine(platform=None)

    def test_a_vault_file_offers_its_cross_host_identity(self):
        ref = self.engine.natural_ref(_file())
        self.assertEqual(ref, {"__natref__": True, "model": "vault.VaultFile",
                               "key": {"bucket__slug": "docs", "key": "statute"}})

    def test_a_model_that_declares_nothing_has_no_natural_reference(self):
        self.assertIsNone(self.engine.natural_ref(
            Community.objects.create(name="C", slug="c")))

    def test_a_broken_chain_disqualifies_the_whole_reference(self):
        """A partial natural key would match the wrong row — worse than none."""
        orphan = VaultFile(bucket=None, key="loose")
        self.assertIsNone(self.engine.natural_ref(orphan))

    def test_the_export_no_longer_carries_a_bare_primary_key(self):
        """THE regression. A raw pk is not a neutral default; it is corruption."""
        community = Community.objects.create(name="C", slug="c", statute=_file())
        fields = self.engine.serialize_object(community)["fields"]

        self.assertIn("statute", fields)
        self.assertTrue(fields["statute"].get("__natref__"))
        self.assertNotIn("statute_id", fields,
                         "the raw pk would be reattached to a different file")

    def test_a_null_reference_stays_null(self):
        community = Community.objects.create(name="C", slug="c")
        self.assertIsNone(self.engine.serialize_object(community)["fields"]["statute"])


class RestoreResolutionTests(TestCase):

    def setUp(self):
        self.service = SyncService(platform=None, apps_to_sync=["socialhub"])
        self.model = Community
        self.field_value = {
            "__natref__": True, "model": "vault.VaultFile",
            "key": {"bucket__slug": "docs", "key": "statute"}}

    def test_it_resolves_to_the_right_file_when_the_bucket_was_mirrored(self):
        _file(key="decoy")           # a file that would win on a stale pk
        wanted = _file(key="statute")
        resolved = self.service._resolve_fields(self.model, {"statute": self.field_value})
        self.assertEqual(resolved["statute"], wanted)

    def test_an_absent_file_drops_a_nullable_field_to_null(self):
        resolved = self.service._resolve_fields(self.model, {"statute": self.field_value})
        self.assertIsNone(resolved["statute"])

    def test_an_absent_file_raises_for_a_REQUIRED_field(self):
        """So the caller can skip the row instead of losing the whole restore."""
        model = django_apps.get_model("kanban", "MissionAttachment")
        with self.assertRaises(UnresolvableReference):
            self.service._resolve_fields(model, {"vault_file": self.field_value})

    def test_an_ambiguous_natural_key_is_treated_as_unresolvable(self):
        """Never guess. Resolving to an arbitrary match is the original bug."""
        service = self.service
        value = {"__natref__": True, "model": "vault.VaultFile",
                 "key": {"key": "statute"}}     # no bucket — matches many
        _file(key="statute", bucket_slug="a")
        _file(key="statute", bucket_slug="b")
        self.assertIsNone(service._resolve_fields(self.model, {"statute": value})["statute"])


class BackupSetTests(TestCase):

    def test_vault_is_not_in_the_backup_set(self):
        """The premise of everything above. If vault were ever synced, these
        references would resolve the ordinary way and the natural key would be
        belt and braces rather than the only mechanism."""
        from django.conf import settings

        self.assertNotIn("vault", settings.APPS_TO_SYNC)

    def test_no_vault_model_became_backup_eligible(self):
        """Cross-check of vault/apps.py's guard, from the backup side.

        Stated here as well as in tests_peering because the consequence lives
        here: eligibility is decided by this module, and a credential model
        entering the archive is the incident that guard exists to prevent.
        """
        engine = BackupEngine(platform=None)
        for model in django_apps.get_app_config("vault").get_models():
            with self.subTest(model=model.__name__):
                self.assertFalse(
                    engine.is_backup_model(model),
                    f"{model._meta.label} is now backup-eligible — a vault model "
                    f"in a pullable archive can carry peering credentials")
