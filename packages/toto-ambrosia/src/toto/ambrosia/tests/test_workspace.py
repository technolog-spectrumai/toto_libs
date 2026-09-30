"""Workspaces, the file tree, and the file operations behind the explorer."""

import unittest

from django.apps import apps

from django.core.exceptions import ValidationError

from toto.vault.models import Bucket, VaultDirectory, VaultFile

from toto.ambrosia import filetree, services
from toto.ambrosia.models import Workspace, WorkspaceKind

from .base import AmbrosiaTestCase


class WorkspaceCreationTests(AmbrosiaTestCase):
    def test_creating_a_workspace_provisions_its_bucket_and_root(self):
        ws = self.make_workspace(name="Experiments")
        self.assertIsNotNone(ws.bucket)
        self.assertEqual(ws.bucket.owner, self.owner)
        self.assertIsNotNone(ws.root_directory)
        self.assertEqual(ws.root_directory.bucket, ws.bucket)

    def test_a_new_python_workspace_starts_with_a_main_file(self):
        ws = self.make_workspace()
        names = list(VaultFile.objects.filter(bucket=ws.bucket)
                     .values_list("title", flat=True))
        self.assertEqual(names, ["main.py"])

    def test_the_starter_file_is_typed_python_so_it_opens_here(self):
        ws = self.make_workspace()
        main = VaultFile.objects.get(bucket=ws.bucket)
        self.assertEqual(main.file_type, "python")

    def test_two_workspaces_of_the_same_name_get_distinct_slugs(self):
        a = self.make_workspace(name="Same")
        b = self.make_workspace(name="Same")
        self.assertNotEqual(a.slug, b.slug)
        self.assertNotEqual(a.bucket.slug, b.bucket.slug)

    def test_a_workspace_needs_a_name(self):
        bucket = self.make_bucket()
        with self.assertRaises(ValidationError):
            services.create_workspace(owner=self.owner, name="   ",
                                      bucket=bucket, new_directory_name="x")

    def test_ambrosia_creates_no_buckets(self):
        before = Bucket.objects.count()
        self.make_workspace()
        # make_workspace makes exactly one bucket itself; create_workspace makes
        # none. A workspace lives in a bucket you already own.
        self.assertEqual(Bucket.objects.count(), before + 1)

    def test_a_bucket_you_do_not_own_is_refused(self):
        theirs = self.make_bucket(owner=self.other, name="Not yours")
        with self.assertRaises(ValidationError) as ctx:
            services.create_workspace(owner=self.owner, name="Sneaky",
                                      bucket=theirs, new_directory_name="x")
        self.assertIn("not yours", str(ctx.exception).lower())

    def test_a_folder_from_another_bucket_is_refused(self):
        a, b = self.make_bucket(name="A"), self.make_bucket(name="B")
        elsewhere = VaultDirectory.objects.create(
            name="elsewhere", bucket=b, owner=self.owner)
        with self.assertRaises(ValidationError):
            services.create_workspace(owner=self.owner, name="Mixed",
                                      bucket=a, directory=elsewhere)

    def test_neither_a_folder_nor_a_name_is_refused(self):
        # Adopting the bucket root would make the tree the whole bucket and the
        # destroy button catastrophic.
        bucket = self.make_bucket()
        with self.assertRaises(ValidationError) as ctx:
            services.create_workspace(owner=self.owner, name="Rootless",
                                      bucket=bucket)
        self.assertIn("folder", str(ctx.exception).lower())

    def test_a_new_folder_is_created_where_asked(self):
        bucket = self.make_bucket()
        parent = VaultDirectory.objects.create(
            name="projects", bucket=bucket, owner=self.owner)
        ws = services.create_workspace(
            owner=self.owner, name="Nested", bucket=bucket,
            directory=parent, new_directory_name="nested")
        self.assertEqual(ws.root_directory.name, "nested")
        self.assertEqual(ws.root_directory.parent_id, parent.pk)

    def test_an_existing_folder_can_be_adopted(self):
        bucket = self.make_bucket()
        existing = VaultDirectory.objects.create(
            name="already here", bucket=bucket, owner=self.owner)
        ws = services.create_workspace(
            owner=self.owner, name="Adopted", bucket=bucket, directory=existing)
        self.assertEqual(ws.root_directory_id, existing.pk)

    def test_adopting_a_folder_that_already_has_code_adds_no_starter_file(self):
        bucket = self.make_bucket()
        existing = VaultDirectory.objects.create(
            name="has code", bucket=bucket, owner=self.owner)
        VaultFile.objects.create(
            owner=self.owner, title="existing.py", key="existing-py",
            file_type="python", bucket=bucket, directory=existing)
        services.create_workspace(owner=self.owner, name="Adopted",
                                  bucket=bucket, directory=existing)
        titles = set(VaultFile.objects.filter(directory=existing)
                     .values_list("title", flat=True))
        self.assertEqual(titles, {"existing.py"}, "main.py was forced in")

    def test_one_folder_cannot_host_two_workspaces(self):
        bucket = self.make_bucket()
        folder = VaultDirectory.objects.create(
            name="shared", bucket=bucket, owner=self.owner)
        services.create_workspace(owner=self.owner, name="First",
                                  bucket=bucket, directory=folder)
        with self.assertRaises(ValidationError) as ctx:
            services.create_workspace(owner=self.owner, name="Second",
                                      bucket=bucket, directory=folder)
        self.assertIn("already the folder", str(ctx.exception).lower())

    def test_one_bucket_holds_many_workspaces(self):
        bucket = self.make_bucket()
        a = self.make_workspace(name="Alpha", bucket=bucket,
                                new_directory_name="alpha")
        b = self.make_workspace(name="Beta", bucket=bucket,
                                new_directory_name="beta")
        self.assertEqual(a.bucket_id, b.bucket_id)
        self.assertNotEqual(a.root_directory_id, b.root_directory_id)

    def test_closing_a_workspace_leaves_the_files_alone(self):
        ws = self.make_workspace()
        root_pk, bucket_pk = ws.root_directory_id, ws.bucket_id
        services.close_workspace(workspace=ws, user=self.owner)
        self.assertTrue(Bucket.objects.filter(pk=bucket_pk).exists())
        self.assertTrue(VaultDirectory.objects.filter(pk=root_pk).exists())
        self.assertTrue(VaultFile.objects.filter(directory_id=root_pk).exists())


class DestroyWorkspaceTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        self.bucket = self.make_bucket()
        self.ws = self.make_workspace(bucket=self.bucket,
                                      new_directory_name="project")
        self.sub = services.create_directory(
            workspace=self.ws, user=self.owner, name="pkg")
        services.create_file(workspace=self.ws, user=self.owner,
                             filename="inner.py", directory=self.sub)

    def test_destroying_removes_every_file_in_the_folder(self):
        result = services.destroy_workspace(workspace=self.ws, user=self.owner)
        self.assertEqual(result["files"], 2)      # main.py + inner.py
        self.assertEqual(result["folders"], 1)    # pkg
        self.assertFalse(VaultFile.objects.filter(bucket=self.bucket).exists())

    def test_destroying_moves_the_files_to_the_trash(self):
        # The vault's trash (2026-10-01): the folders go for good, the files
        # wait in their owner's trash with their bytes.
        services.destroy_workspace(workspace=self.ws, user=self.owner)
        trashed = VaultFile.all_objects.filter(bucket=self.bucket)
        self.assertEqual(trashed.count(), 2)
        for row in trashed:
            self.assertIsNotNone(row.trashed_at)
            self.assertEqual(row.trashed_by, self.owner)
            self.assertTrue(row.file.storage.exists(row.file.name))

    def test_destroying_removes_the_folder_and_its_subfolders(self):
        root_pk, sub_pk = self.ws.root_directory_id, self.sub.pk
        services.destroy_workspace(workspace=self.ws, user=self.owner)
        self.assertFalse(VaultDirectory.objects.filter(pk=root_pk).exists())
        self.assertFalse(VaultDirectory.objects.filter(pk=sub_pk).exists())

    def test_destroying_removes_the_workspace_row(self):
        pk = self.ws.pk
        services.destroy_workspace(workspace=self.ws, user=self.owner)
        self.assertFalse(Workspace.objects.filter(pk=pk).exists())

    def test_the_bucket_itself_survives(self):
        services.destroy_workspace(workspace=self.ws, user=self.owner)
        self.assertTrue(Bucket.objects.filter(pk=self.bucket.pk).exists())

    def test_no_file_is_orphaned_into_the_bucket_root(self):
        # VaultFile.directory is SET_NULL, so deleting the directory first would
        # not delete its files — it would spill them into the bucket root, where
        # they reappear in the vault browser as loose files nobody meant to keep.
        services.destroy_workspace(workspace=self.ws, user=self.owner)
        self.assertFalse(
            VaultFile.objects.filter(bucket=self.bucket, directory__isnull=True)
            .exists())

    def test_a_neighbouring_workspace_is_untouched(self):
        neighbour = self.make_workspace(name="Neighbour", bucket=self.bucket,
                                        new_directory_name="neighbour")
        services.destroy_workspace(workspace=self.ws, user=self.owner)
        neighbour.refresh_from_db()
        self.assertTrue(
            VaultFile.objects.filter(directory=neighbour.root_directory).exists())

    def test_only_the_owner_may_destroy(self):
        with self.assertRaises(ValidationError):
            services.destroy_workspace(workspace=self.ws, user=self.other)
        self.assertTrue(VaultFile.objects.filter(bucket=self.bucket).exists())

    def test_deleting_the_folder_in_the_vault_takes_the_workspace_with_it(self):
        pk = self.ws.pk
        self.ws.root_directory.delete()
        self.assertFalse(Workspace.objects.filter(pk=pk).exists())


class FileOperationTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace()

    def test_a_new_file_lands_in_the_workspace_bucket(self):
        f = services.create_file(workspace=self.ws, user=self.owner,
                                 filename="helper.py")
        self.assertEqual(f.bucket, self.ws.bucket)
        self.assertEqual(f.directory, self.ws.root_directory)

    def test_the_extension_decides_the_file_type(self):
        cases = {"a.py": "python", "b.txt": "text", "c.json": "json",
                 "d.yaml": "yaml", "e.tex": "latex", "f.bib": "bib",
                 "g.csv": "csv"}
        for name, expected in cases.items():
            f = services.create_file(workspace=self.ws, user=self.owner,
                                     filename=name)
            self.assertEqual(f.file_type, expected, name)

    def test_a_duplicate_name_in_the_same_folder_is_refused(self):
        services.create_file(workspace=self.ws, user=self.owner, filename="dup.py")
        with self.assertRaises(ValidationError):
            services.create_file(workspace=self.ws, user=self.owner,
                                 filename="dup.py")

    def test_a_file_name_cannot_carry_a_path(self):
        with self.assertRaises(ValidationError):
            services.create_file(workspace=self.ws, user=self.owner,
                                 filename="../escape.py")

    def test_files_with_colliding_slugs_both_get_created(self):
        # VaultFile.save() raises on a duplicate key, so the service has to
        # pre-assign a unique one rather than let the collision surface.
        a = services.create_file(workspace=self.ws, user=self.owner,
                                 filename="report.py")
        folder = services.create_directory(workspace=self.ws, user=self.owner,
                                           name="sub")
        b = services.create_file(workspace=self.ws, user=self.owner,
                                 filename="report.py", directory=folder)
        self.assertNotEqual(a.key, b.key)

    def test_reading_and_writing_round_trips(self):
        f = services.create_file(workspace=self.ws, user=self.owner,
                                 filename="round.py")
        services.write_file(vault_file=f, content="x = 1\n")
        self.assertEqual(services.read_file(f)[0], "x = 1\n")

    def test_writing_updates_the_recorded_size(self):
        f = services.create_file(workspace=self.ws, user=self.owner,
                                 filename="size.py")
        services.write_file(vault_file=f, content="y = 2\n" * 100)
        f.refresh_from_db()
        self.assertGreater(f.file_size_bytes, 100)

    def test_renaming_moves_the_file_type_with_the_extension(self):
        # Otherwise renaming notes.txt to notes.py leaves it opening in the wrong
        # editor, which is a rename that lies.
        f = services.create_file(workspace=self.ws, user=self.owner,
                                 filename="notes.txt")
        self.assertEqual(f.file_type, "text")
        services.rename_file(workspace=self.ws, vault_file=f, name="notes.py")
        f.refresh_from_db()
        self.assertEqual(f.file_type, "python")

    def test_a_folder_cannot_be_created_outside_the_workspace(self):
        other = self.make_workspace(name="Elsewhere")
        with self.assertRaises(ValidationError):
            services.create_file(workspace=self.ws, user=self.owner,
                                 filename="x.py",
                                 directory=other.root_directory)

    def test_an_encrypted_file_is_never_read_or_written_here(self):
        f = services.create_file(workspace=self.ws, user=self.owner,
                                 filename="secret.py")
        f.is_encrypted = True
        f.save(update_fields=["is_encrypted"])
        with self.assertRaises(ValidationError):
            services.read_file(f)
        with self.assertRaises(ValidationError):
            services.write_file(vault_file=f, content="nope")


class FileTreeTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace()

    def test_the_tree_is_rooted_at_the_bucket(self):
        # The workspace folder is a row like any other, at depth 0, because the
        # tree shows the whole bucket. That is the reversal this rework made:
        # a shared preamble does not live inside one workspace's folder.
        rows = {r["name"]: r for r in filetree.flatten(self.ws)}
        self.assertEqual(rows[self.ws.root_directory.name]["depth"], 0)
        self.assertEqual(rows["main.py"]["depth"], 1)

    def test_nesting_increases_depth(self):
        outer = services.create_directory(workspace=self.ws, user=self.owner,
                                          name="pkg")
        inner = services.create_directory(workspace=self.ws, user=self.owner,
                                          name="deep", parent=outer)
        services.create_file(workspace=self.ws, user=self.owner,
                             filename="deep.py", directory=inner)
        rows = {r["name"]: r for r in filetree.flatten(self.ws)}
        self.assertEqual(rows[self.ws.root_directory.name]["depth"], 0)
        self.assertEqual(rows["pkg"]["depth"], 1)
        self.assertEqual(rows["deep"]["depth"], 2)
        self.assertEqual(rows["deep.py"]["depth"], 3)

    def test_every_row_points_at_its_parent(self):
        outer = services.create_directory(workspace=self.ws, user=self.owner,
                                          name="pkg")
        services.create_file(workspace=self.ws, user=self.owner,
                             filename="in_pkg.py", directory=outer)
        rows = {r["name"]: r for r in filetree.flatten(self.ws)}
        self.assertEqual(rows["in_pkg.py"]["pid"], outer.pk)

    def test_directories_come_before_the_files_beside_them(self):
        services.create_directory(workspace=self.ws, user=self.owner, name="zzz")
        rows = filetree.flatten(self.ws)
        kinds = [r["t"] for r in rows]
        self.assertEqual(kinds[0], "dir",
                         "a folder named zzz still sorts above main.py")

    def test_the_tree_never_leaks_another_BUCKET(self):
        # The one boundary that is hard. A workspace in a different bucket is
        # invisible, and its files 404 by pk — see test_latex's endpoint tests.
        other = self.make_workspace(name="Not mine",
                                    bucket=self.make_bucket(name="Theirs"))
        services.create_file(workspace=other, user=self.owner,
                             filename="theirs.py")
        names = [r["name"] for r in filetree.flatten(self.ws)]
        self.assertNotIn("theirs.py", names)

    def test_a_neighbour_in_the_same_bucket_IS_shown(self):
        # Deliberate, and the reverse of what this did before. Two workspaces
        # in one bucket are two views onto one project — sharing a preamble is
        # the point, and hiding half the bucket only hides why a compile failed.
        neighbour = self.make_workspace(name="Neighbour", bucket=self.ws.bucket,
                                        new_directory_name="neighbour")
        services.create_file(workspace=neighbour, user=self.owner,
                             filename="neighbour.py")
        names = [r["name"] for r in filetree.flatten(self.ws)]
        self.assertIn("neighbour.py", names)
        self.assertIn("neighbour", names)

    def test_loose_files_at_the_bucket_root_are_shown(self):
        # These used to be invisible, which is how a `preamble.sty` at the
        # bucket root could be staged and yet appear nowhere.
        VaultFile.objects.create(
            owner=self.owner, title="loose.py", key="loose-py",
            file_type="python", bucket=self.ws.bucket, directory=None)
        rows = {r["name"]: r for r in filetree.flatten(self.ws)}
        self.assertIn("loose.py", rows)
        self.assertEqual(rows["loose.py"]["depth"], 0)

    def test_a_workspace_with_no_bucket_has_an_empty_tree(self):
        self.ws.bucket = None
        self.assertEqual(filetree.flatten(self.ws), [])

    def test_generated_files_are_marked_read_only(self):
        # a bare build/ folder — the base's read-only rule keys on the NAME;
        # producing one is texlab's business and not under test here
        from toto.vault.models import VaultDirectory
        build = VaultDirectory.objects.create(
            name="build", bucket=self.ws.bucket, owner=self.owner,
            parent=self.ws.root_directory)
        log = VaultFile.objects.create(
            owner=self.owner, title="main.log", key="main-log",
            file_type="text", bucket=self.ws.bucket, directory=build)
        rows = {r["name"]: r for r in filetree.flatten(self.ws)}
        self.assertTrue(rows["main.log"]["readonly"])
        self.assertTrue(rows["main.log"]["editable"], "a log must still open")
        self.assertFalse(rows["main.py"]["readonly"])
        self.assertTrue(filetree.is_artifact(log))

    def test_python_files_are_marked_runnable_and_editable(self):
        rows = {r["name"]: r for r in filetree.flatten(self.ws)}
        self.assertTrue(rows["main.py"]["runnable"])
        self.assertTrue(rows["main.py"]["editable"])

    def test_an_encrypted_file_is_neither_editable_nor_runnable(self):
        f = VaultFile.objects.get(bucket=self.ws.bucket, title="main.py")
        f.is_encrypted = True
        f.save(update_fields=["is_encrypted"])
        row = [r for r in filetree.flatten(self.ws) if r["name"] == "main.py"][0]
        self.assertFalse(row["editable"])
        self.assertFalse(row["runnable"])
        self.assertTrue(row["encrypted"])

    def test_a_binary_type_is_listed_but_not_editable(self):
        # A workspace that hid its own data files would be lying about what it
        # contains, so they show — greyed, and they do not open.
        f = VaultFile.objects.create(
            owner=self.owner, title="chart.png", key="chart-png",
            file_type="image", bucket=self.ws.bucket,
            directory=self.ws.root_directory)
        row = [r for r in filetree.flatten(self.ws) if r["name"] == "chart.png"][0]
        self.assertFalse(row["editable"])
        self.assertEqual(row["file_type"], "image")


# Every test below asks the vault which editor opens a .py file, and the answer
# comes from the PYTHON workspace app, toto.dracena. It is a host portion on
# zenobia and behind BUILD_ANASTASIA there, so a build without Compute Capsules
# registers no plugin for "python" and nothing here has a subject.
#
# The condition read `is_installed("toto.dracena") or is_installed("toto.dracena")`
# until 2026-09-09 — the same call twice, a rename that edited both halves of a
# disjunction meant to name antaresia and dracena. It was harmless only because
# the duplicate agreed with the original; a second app name would have silently
# widened the skip.
@unittest.skipUnless(
    apps.is_installed("toto.dracena"),
    "no Python workspace app installed (toto.dracena rides with Compute Capsules)")
class EditorPluginTests(AmbrosiaTestCase):
    def test_ambrosia_claims_the_python_file_type(self):
        from toto.vault.plugins import VaultEditorPlugin

        plugin = VaultEditorPlugin.for_file_type("python")
        self.assertIsNotNone(plugin, "no plugin claims .py — the Edit link vanishes")
        self.assertEqual(type(plugin).__name__, "PythonWorkspacePlugin")

    def test_the_plugin_key_equals_its_file_type(self):
        # The registry stores by key and lookups go by file_type. A mismatch
        # makes the plugin silently unreachable with no error anywhere.
        from toto.dracena.plugins.vault_editor_plugins import PythonWorkspacePlugin

        self.assertEqual(PythonWorkspacePlugin.get_key(),
                         PythonWorkspacePlugin.file_type)

    def test_a_workspace_file_opens_in_its_workspace(self):
        from toto.vault.plugins import VaultEditorPlugin

        ws = self.make_workspace()
        main = VaultFile.objects.get(directory=ws.root_directory)
        url = VaultEditorPlugin.for_file_type("python").get_editor_url(main)
        self.assertIn(ws.slug, url)
        self.assertIn(f"file={main.pk}", url)

    def test_a_file_in_a_subfolder_still_finds_its_workspace(self):
        from toto.vault.plugins import VaultEditorPlugin

        ws = self.make_workspace()
        sub = services.create_directory(workspace=ws, user=self.owner,
                                        name="pkg")
        deep = services.create_file(workspace=ws, user=self.owner,
                                    filename="deep.py", directory=sub)
        url = VaultEditorPlugin.for_file_type("python").get_editor_url(deep)
        self.assertIn(ws.slug, url)

    def test_the_edit_link_opens_the_RIGHT_workspace_in_a_shared_bucket(self):
        # Matching on the bucket would return an arbitrary neighbour, and the
        # room it opened would then refuse the file id as out of scope.
        from toto.vault.plugins import VaultEditorPlugin

        bucket = self.make_bucket()
        first = self.make_workspace(name="First", bucket=bucket,
                                    new_directory_name="first")
        second = self.make_workspace(name="Second", bucket=bucket,
                                     new_directory_name="second")
        mine = VaultFile.objects.get(directory=second.root_directory)
        url = VaultEditorPlugin.for_file_type("python").get_editor_url(mine)
        self.assertIn(second.slug, url)
        self.assertNotIn(first.slug, url)

    def test_a_stray_python_file_still_gets_an_editor(self):
        # A .py file outside any workspace must not lose its Edit button.
        from toto.vault.plugins import VaultEditorPlugin

        bucket = Bucket.objects.create(
            name="Loose", owner=self.owner, slug="loose")
        stray = VaultFile.objects.create(
            owner=self.owner, title="stray.py", key="stray-py",
            file_type="python", bucket=bucket)
        url = VaultEditorPlugin.for_file_type("python").get_editor_url(stray)
        self.assertTrue(url)

    def test_a_file_beside_a_workspace_does_not_borrow_it(self):
        # Same bucket, but outside every workspace folder: the fallback editor,
        # not somebody's project.
        from toto.vault.plugins import VaultEditorPlugin

        ws = self.make_workspace()
        beside = VaultFile.objects.create(
            owner=self.owner, title="beside.py", key="beside-py",
            file_type="python", bucket=ws.bucket, directory=None)
        url = VaultEditorPlugin.for_file_type("python").get_editor_url(beside)
        self.assertNotIn(ws.slug, url)


class WorkspaceCapTests(AmbrosiaTestCase):
    """How many workspaces one person may hold, and who decides.

    The cap is a row in the admin rather than a Django setting, because it is
    an operator's decision about this deployment and changing it should not
    need a redeploy. These tests pin the two halves that matter: the DEFAULT is
    5, and the admin value is what actually binds — a test that only checked
    the default would pass just as well against a hard-coded 5.
    """

    def _fill_to(self, count, owner=None):
        for n in range(count):
            self.make_workspace(owner=owner, name=f"ws{n}")

    def test_the_shipped_default_is_five(self):
        from toto.ambrosia.models import AmbrosiaSettings

        # Read through the accessor, not the field: a fresh install has no row
        # at all, and "what does a brand new platform do" is the question.
        self.assertEqual(AmbrosiaSettings.workspace_cap(), 5)

    def test_a_sixth_workspace_is_refused_with_a_sentence(self):
        self._fill_to(5)
        with self.assertRaises(ValidationError) as caught:
            self.make_workspace(name="one too many")
        message = "; ".join(caught.exception.messages)
        self.assertIn("5", message)
        self.assertIn("limit", message.lower())
        self.assertEqual(Workspace.objects.filter(owner=self.owner).count(), 5)

    def test_the_admin_value_is_what_binds(self):
        from toto.ambrosia.models import AmbrosiaSettings

        settings_row = AmbrosiaSettings.get()
        settings_row.max_workspaces_per_user = 2
        settings_row.save()

        self._fill_to(2)
        with self.assertRaises(ValidationError):
            self.make_workspace(name="third")

        # ...and raising it lets the next one through, which is the half that
        # proves the number is read per call rather than captured at import.
        settings_row.max_workspaces_per_user = 3
        settings_row.save()
        self.assertIsNotNone(self.make_workspace(name="third, allowed now"))

    def test_zero_means_no_limit(self):
        from toto.ambrosia.models import AmbrosiaSettings

        settings_row = AmbrosiaSettings.get()
        settings_row.max_workspaces_per_user = 0
        settings_row.save()
        self._fill_to(7)
        self.assertEqual(Workspace.objects.filter(owner=self.owner).count(), 7)

    def test_destroying_one_frees_its_slot(self):
        """The reason this is a live count and not a quota metric: usage events
        only accumulate, so a deleted workspace would hold its slot forever."""
        self._fill_to(5)
        doomed = Workspace.objects.filter(owner=self.owner).first()
        services.destroy_workspace(workspace=doomed, user=self.owner)
        self.assertIsNotNone(self.make_workspace(name="replacement"))

    def test_the_cap_is_per_person_not_per_platform(self):
        from django.contrib.auth import get_user_model

        self._fill_to(5)
        other = get_user_model().objects.create_user("neighbour", password="x")
        self.assertIsNotNone(self.make_workspace(owner=other, name="theirs"))

    def test_a_refused_creation_leaves_no_folder_behind(self):
        """The guard runs before the block that may create a VaultDirectory."""
        self._fill_to(5)
        bucket = self.make_bucket()
        before = VaultDirectory.objects.filter(bucket=bucket).count()
        with self.assertRaises(ValidationError):
            services.create_workspace(owner=self.owner, name="nope",
                                      bucket=bucket, new_directory_name="orphan")
        self.assertEqual(VaultDirectory.objects.filter(bucket=bucket).count(), before)
        self.assertFalse(
            VaultDirectory.objects.filter(bucket=bucket, name="orphan").exists())
