"""The `data_mesh` group: seeded on every fresh database, and re-creatable.

`toto.api.cors.in_data_mesh` gates every mesh read on membership of exactly
this group. api's hand-written migration `0002_data_mesh_group` seeds it (the
2026-10-01 reset kept that step on top of the fresh initial), and
`init_data` calls `ensure_data_mesh_group()` too, so the seed does not live
only in a migration.
"""

from django.contrib.auth.models import Group
from django.test import TestCase

from toto.api.cors import DATA_MESH_GROUP, ensure_data_mesh_group


class DataMeshGroupTests(TestCase):
    def test_a_fresh_database_has_the_group(self):
        """The test database is built by migrating, so this is the migration's work."""
        self.assertTrue(Group.objects.filter(name=DATA_MESH_GROUP).exists())

    def test_ensure_recreates_a_deleted_group_once(self):
        Group.objects.filter(name=DATA_MESH_GROUP).delete()
        first = ensure_data_mesh_group()
        second = ensure_data_mesh_group()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Group.objects.filter(name=DATA_MESH_GROUP).count(), 1)
