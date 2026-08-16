"""The project wiki: the tree, the permissions, and the bridge into cyprian.

Two things are being defended here.

The first is ordinary: a page belongs to a project, pages nest, slugs are unique
per project rather than globally, and reading follows project membership while
writing follows `can_manage_tasks`. The view this replaced had no login gate and
no visibility filter at all — any page was readable by anyone who guessed a pk,
anonymously, even when its mission was PRIVATE — so those tests are here to keep
the fix rather than to describe it.

The second is the bridge, and it is the reason this file is worth reading.
Cyprian's writer opens for `VaultFile.owner` and 404s for everyone else; the
bridge widens that to "the project's team". Everything that widening depends on
is asserted below, most importantly that the trust anchor
(`DocumentationPage.vault_file`) is not settable by anyone who can edit a page —
because a member who could set it could point a page at any document on the
instance and be authorised onto it.
"""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.kanban.forms import WikiPageForm
from toto.kanban.models import (
    Campaign, DocumentationPage, Mission, Practitioner, Project,
    ProjectCommitment,
)
from toto.people.models import Person
from toto.vault.models import Bucket, VaultFile

User = get_user_model()

storage_override = override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})


def _person(username, **kwargs):
    user = User.objects.create_user(username=username, password="pass", **kwargs)
    person = Person.objects.create(
        user=user, display_name=username.title(), email=f"{username}@x.com")
    return user, person


@storage_override
class WikiWorld(TestCase):
    """One project with a team, a second project nobody in it belongs to."""

    def setUp(self):
        # A document's bytes are a real file and the deployed MEDIA_ROOT is a
        # root-owned bind mount — same reason cyprian's own base class does this.
        self._media = tempfile.mkdtemp(prefix="kanban-wiki-test-")
        self._media_override = override_settings(MEDIA_ROOT=self._media)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(shutil.rmtree, self._media, ignore_errors=True)

        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)

        self.lead_user, self.lead = _person("lead")
        self.member_user, self.member = _person("member")
        self.reader_user, self.reader = _person("reader")
        self.stranger_user, self.stranger = _person("stranger")
        self.staff_user, self.staff = _person("staff", is_staff=True)

        self.project = Project.objects.create(name="P", project_lead=self.lead)
        seat = Practitioner.objects.create(person=self.member, role="contributor")
        ProjectCommitment.objects.create(
            practitioner=seat, project=self.project, hours_per_day=4)

        # A reader with an INACTIVE commitment: on the project, not on the team.
        reader_seat = Practitioner.objects.create(person=self.reader, role="observer")
        ProjectCommitment.objects.create(
            practitioner=reader_seat, project=self.project,
            hours_per_day=1, is_active=False)

        self.campaign = Campaign.objects.create(project=self.project, name="C")
        self.mission = Mission.objects.create(campaign=self.campaign, title="M")

        self.other_project = Project.objects.create(name="Q", project_lead=self.stranger)

        self.page = DocumentationPage.objects.create(
            project=self.project, title="Getting started", mission=self.mission,
            body_html="<p>hello</p>")

    def url(self, name, *args):
        return reverse(f"kanban:{name}", args=args)


class PageTreeTests(WikiWorld):
    def test_a_page_belongs_to_a_project_and_may_name_no_mission(self):
        page = DocumentationPage.objects.create(project=self.project, title="Free")
        self.assertIsNone(page.mission)
        self.assertEqual(page.project, self.project)

    def test_slug_is_unique_per_project_not_globally(self):
        """The whole point of redeclaring the field: two spaces, one page name."""
        mine = DocumentationPage.objects.create(project=self.project, title="Overview")
        theirs = DocumentationPage.objects.create(
            project=self.other_project, title="Overview")
        self.assertEqual(mine.slug, "overview")
        self.assertEqual(theirs.slug, "overview")

    def test_a_collision_inside_one_project_gets_a_suffix(self):
        first = DocumentationPage.objects.create(project=self.project, title="Notes")
        second = DocumentationPage.objects.create(project=self.project, title="Notes")
        self.assertEqual(first.slug, "notes")
        self.assertNotEqual(second.slug, first.slug)

    def test_saving_a_page_again_keeps_its_own_slug(self):
        """The collision loop must not treat the row as its own clash."""
        self.page.title = "Getting started, revised"
        self.page.save()
        self.assertEqual(self.page.slug, "getting-started")

    def test_ancestors_are_root_first(self):
        mid = DocumentationPage.objects.create(
            project=self.project, title="Mid", parent=self.page)
        leaf = DocumentationPage.objects.create(
            project=self.project, title="Leaf", parent=mid)
        self.assertEqual([p.pk for p in leaf.ancestors()], [self.page.pk, mid.pk])

    def test_a_page_cannot_be_its_own_parent(self):
        self.page.parent = self.page
        with self.assertRaises(ValidationError):
            self.page.full_clean()

    def test_a_loop_is_refused(self):
        child = DocumentationPage.objects.create(
            project=self.project, title="Child", parent=self.page)
        self.page.parent = child
        with self.assertRaises(ValidationError):
            self.page.full_clean()

    def test_a_parent_from_another_project_is_refused(self):
        theirs = DocumentationPage.objects.create(
            project=self.other_project, title="Theirs")
        self.page.parent = theirs
        with self.assertRaises(ValidationError):
            self.page.full_clean()

    def test_a_mission_from_another_project_is_refused(self):
        their_campaign = Campaign.objects.create(project=self.other_project, name="QC")
        their_mission = Mission.objects.create(campaign=their_campaign, title="QM")
        self.page.mission = their_mission
        with self.assertRaises(ValidationError):
            self.page.full_clean()

    def test_deleting_a_mission_keeps_the_prose(self):
        """SET_NULL, not CASCADE: a page outlives what it documents."""
        self.mission.delete()
        self.page.refresh_from_db()
        self.assertIsNone(self.page.mission)
        self.assertEqual(self.page.body_html, "<p>hello</p>")


class PageAccessTests(WikiWorld):
    def test_anonymous_is_redirected_not_served(self):
        """The view this replaced served any page to anyone with a pk."""
        response = self.client.get(
            self.url("wiki_page", self.project.pk, self.page.slug))
        self.assertIn(response.status_code, (302, 403))

    def test_a_stranger_gets_404_not_403(self):
        self.client.force_login(self.stranger_user)
        response = self.client.get(
            self.url("wiki_page", self.project.pk, self.page.slug))
        self.assertEqual(response.status_code, 404)

    def test_a_member_can_read(self):
        self.client.force_login(self.member_user)
        response = self.client.get(
            self.url("wiki_page", self.project.pk, self.page.slug))
        self.assertEqual(response.status_code, 200)

    def test_the_body_is_rendered(self):
        self.client.force_login(self.member_user)
        response = self.client.get(
            self.url("wiki_page", self.project.pk, self.page.slug))
        self.assertContains(response, "hello")

    def test_staff_can_read_any_project(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(
            self.url("wiki_page", self.project.pk, self.page.slug))
        self.assertEqual(response.status_code, 200)

    def test_the_index_lists_the_tree(self):
        self.client.force_login(self.member_user)
        response = self.client.get(self.url("wiki_index", self.project.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Getting started")

    def test_a_stranger_cannot_see_the_index(self):
        self.client.force_login(self.stranger_user)
        response = self.client.get(self.url("wiki_index", self.project.pk))
        self.assertEqual(response.status_code, 404)

    def test_the_legacy_url_redirects_permanently(self):
        """Admin's "View on site" and a year of mission-page links reversed it."""
        self.client.force_login(self.member_user)
        response = self.client.get(
            self.url("documentation_page_detail", self.page.pk))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], self.page.get_absolute_url())


class PageWriteAccessTests(WikiWorld):
    def test_the_lead_reads_but_a_seatless_lead_does_not_write(self):
        """Where can_read_project and can_manage_tasks actually diverge.

        `can_manage_tasks` wants staff, a project auditor, or an ACTIVE
        Practitioner with an active commitment — a project_lead with no
        Practitioner seat is none of those. That is not a wiki rule; it is the
        board's rule, and the same lead cannot move a task either. Reusing it
        was the point, so the quirk comes along and is pinned here rather than
        quietly special-cased for pages. In practice leads have seats: ingress
        creates one, and migration 0003 lifted one for every lead that lacked it.
        """
        self.client.force_login(self.lead_user)
        self.assertEqual(
            self.client.get(self.url("wiki_page", self.project.pk, self.page.slug))
            .status_code, 200)
        self.assertEqual(
            self.client.get(self.url("wiki_page_create", self.project.pk))
            .status_code, 403)

    def test_an_inactive_commitment_is_off_the_project_entirely(self):
        """Not a reader: ProjectListView's own filter requires is_active."""
        self.client.force_login(self.reader_user)
        self.assertEqual(
            self.client.get(self.url("wiki_page", self.project.pk, self.page.slug))
            .status_code, 404)

    def test_a_member_may_create(self):
        self.client.force_login(self.member_user)
        response = self.client.post(
            self.url("wiki_page_create", self.project.pk),
            {"title": "Runbook", "description": "", "parent": "",
             "mission": "", "is_manual": ""})
        self.assertEqual(response.status_code, 302)
        page = DocumentationPage.objects.get(project=self.project, slug="runbook")
        self.assertEqual(page.project, self.project)

    def test_a_created_page_lands_in_the_right_project(self):
        """form_valid pins the project from the URL, never from the payload."""
        self.client.force_login(self.member_user)
        self.client.post(
            self.url("wiki_page_create", self.project.pk),
            {"title": "Scoped", "description": "", "parent": "",
             "mission": "", "is_manual": ""})
        self.assertFalse(
            DocumentationPage.objects.filter(
                project=self.other_project, slug="scoped").exists())

    def test_a_stranger_may_not_create(self):
        self.client.force_login(self.stranger_user)
        response = self.client.post(
            self.url("wiki_page_create", self.project.pk),
            {"title": "Nope", "description": "", "parent": "",
             "mission": "", "is_manual": ""})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            DocumentationPage.objects.filter(slug="nope").exists())

    def test_deleting_takes_the_children(self):
        child = DocumentationPage.objects.create(
            project=self.project, title="Child", parent=self.page)
        self.client.force_login(self.member_user)
        response = self.client.post(
            self.url("wiki_page_delete", self.project.pk, self.page.slug))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(DocumentationPage.objects.filter(pk=child.pk).exists())


class WikiPageFormTests(WikiWorld):
    def test_the_form_has_no_vault_file_field(self):
        """The trust anchor must not be settable by anyone who can edit a page.

        A project member who could POST `vault_file` could point their page at
        any document-typed VaultFile on the instance, and the cyprian bridge
        would then authorise them onto it — reading and rewriting somebody
        else's private document. `editable=False` on the model already keeps it
        out; this asserts nobody has quietly added it back.
        """
        form = WikiPageForm(project=self.project)
        self.assertNotIn("vault_file", form.fields)
        self.assertNotIn("body_html", form.fields)

    def test_posting_vault_file_is_ignored(self):
        other = VaultFile.objects.create(
            owner=self.stranger_user, title="secret.xml", key="secret",
            file_type="document",
            bucket=Bucket.objects.create(name="S", slug="s",
                                         owner=self.stranger_user,
                                         storage_backend="local"))
        form = WikiPageForm(
            data={"title": "Sneaky", "description": "", "parent": "",
                  "mission": "", "vault_file": other.pk},
            project=self.project)
        self.assertTrue(form.is_valid(), form.errors)
        page = form.save(commit=False)
        page.project = self.project
        page.save()
        self.assertIsNone(page.vault_file_id)

    def test_parent_choices_exclude_the_page_and_its_descendants(self):
        child = DocumentationPage.objects.create(
            project=self.project, title="Child", parent=self.page)
        grandchild = DocumentationPage.objects.create(
            project=self.project, title="Grandchild", parent=child)
        form = WikiPageForm(instance=self.page, project=self.project)
        offered = set(form.fields["parent"].queryset.values_list("pk", flat=True))
        self.assertNotIn(self.page.pk, offered)
        self.assertNotIn(child.pk, offered)
        self.assertNotIn(grandchild.pk, offered)

    def test_parent_choices_are_scoped_to_the_project(self):
        theirs = DocumentationPage.objects.create(
            project=self.other_project, title="Theirs")
        form = WikiPageForm(project=self.project)
        self.assertNotIn(
            theirs.pk,
            set(form.fields["parent"].queryset.values_list("pk", flat=True)))


class BridgeTests(WikiWorld):
    """The seam into cyprian. Skipped where the writer is not installed."""

    def setUp(self):
        super().setUp()
        from django.apps import apps
        if not apps.is_installed("toto.cyprian"):
            self.skipTest("no document editor on this host")

    def _write(self, user):
        self.client.force_login(user)
        return self.client.get(
            self.url("wiki_page_write", self.project.pk, self.page.slug))

    def test_write_mints_the_document_once(self):
        response = self._write(self.member_user)
        self.assertEqual(response.status_code, 302)
        self.page.refresh_from_db()
        first = self.page.vault_file_id
        self.assertIsNotNone(first)

        self._write(self.staff_user)
        self.page.refresh_from_db()
        self.assertEqual(self.page.vault_file_id, first)

    def test_the_document_is_seeded_from_the_body_once_only(self):
        """Re-seeding on each open would revert whatever the last save wrote."""
        from toto.cyprian import document_format
        from toto.cyprian.bridge import read_raw

        self._write(self.member_user)
        self.page.refresh_from_db()
        self.assertIn("hello", read_raw(self.page.vault_file))

        self.page.body_html = "<p>changed elsewhere</p>"
        self.page.save(update_fields=["body_html"])
        self._write(self.member_user)
        self.page.refresh_from_db()
        document = document_format.loads(read_raw(self.page.vault_file))
        self.assertIn("hello", document.content)
        self.assertNotIn("changed elsewhere", document.content)

    def test_the_file_is_owned_by_the_project_lead(self):
        """Not by whoever clicked first: a shared page outlives its author."""
        self._write(self.member_user)
        self.page.refresh_from_db()
        self.assertEqual(self.page.vault_file.owner, self.lead_user)

    def test_a_stranger_cannot_reach_the_writer(self):
        self._write(self.member_user)
        self.page.refresh_from_db()

        self.client.force_login(self.stranger_user)
        response = self.client.get(
            reverse("cyprian:edit", args=[self.page.vault_file_id]))
        self.assertEqual(response.status_code, 404)

    def test_a_team_member_can_open_a_file_they_do_not_own(self):
        """The whole point: cyprian is owner-only without the bridge."""
        self._write(self.member_user)
        self.page.refresh_from_db()
        self.assertNotEqual(self.page.vault_file.owner, self.member_user)

        self.client.force_login(self.member_user)
        response = self.client.get(
            reverse("cyprian:edit", args=[self.page.vault_file_id]))
        self.assertEqual(response.status_code, 200)

    def test_the_lead_reaches_the_writer_as_owner_not_through_the_bridge(self):
        """Worth pinning because it looks like a permission hole and is not.

        A project_lead with no Practitioner seat fails `can_manage_tasks`, so
        the bridge refuses them — and they can still open the writer, because
        the page's file is minted in THEIR vault and cyprian's own ownership
        branch answers first. Nothing is widened here: they could open any file
        they own before this change too.

        There is deliberately no test for "can read the project but cannot open
        the writer". No such actor exists: `can_read_project` grants through
        lead, active commitment, or auditor; the last two both satisfy
        `can_manage_tasks`, and the first owns the file. Writing that test would
        mean inventing a state the permission model cannot produce.
        """
        from toto.kanban.views import can_manage_tasks

        self._write(self.member_user)
        self.page.refresh_from_db()
        self.assertFalse(can_manage_tasks(self.lead_user, self.project))
        self.assertEqual(self.page.vault_file.owner, self.lead_user)

        self.client.force_login(self.lead_user)
        self.assertEqual(
            self.client.get(
                reverse("cyprian:edit", args=[self.page.vault_file_id])
            ).status_code, 200)

    def test_saving_writes_the_body_back_to_the_page(self):
        """body_html is what every host renders — a save that missed it is lost."""
        import json

        self._write(self.member_user)
        self.page.refresh_from_db()

        self.client.force_login(self.member_user)
        response = self.client.post(
            reverse("cyprian:save", args=[self.page.vault_file_id]),
            data=json.dumps({"document": {
                "title": "Getting started",
                "content": "<p>written by the team</p>",
                "meta": {},
            }}),
            content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)

        self.page.refresh_from_db()
        self.assertIn("written by the team", self.page.body_html)

    def test_a_stranger_cannot_save(self):
        import json

        self._write(self.member_user)
        self.page.refresh_from_db()

        self.client.force_login(self.stranger_user)
        response = self.client.post(
            reverse("cyprian:save", args=[self.page.vault_file_id]),
            data=json.dumps({"document": {"content": "<p>defaced</p>", "meta": {}}}),
            content_type="application/json")
        self.assertEqual(response.status_code, 404)

        self.page.refresh_from_db()
        self.assertNotIn("defaced", self.page.body_html)

    def test_a_team_member_can_hold_the_editing_lock(self):
        """The writer let them in; the lock endpoints used to refuse them.

        `version_views._file_for` asked the vault only — owner, staff, ACL,
        public — and a wiki collaborator is none of those. So two members
        editing one page each believed they held it, and the loser's autosave
        met a 423 with no banner to explain it. The vault now asks the owning
        app through VaultAccessPlugin.
        """
        self._write(self.member_user)
        self.page.refresh_from_db()
        self.assertNotEqual(self.page.vault_file.owner, self.member_user)

        self.client.force_login(self.member_user)
        response = self.client.post(
            reverse("vault:lock_acquire", args=[self.page.vault_file_id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["mine"])

    def test_a_team_member_sees_the_pages_history(self):
        self._write(self.member_user)
        self.page.refresh_from_db()

        self.client.force_login(self.member_user)
        response = self.client.get(
            reverse("vault:version_list", args=[self.page.vault_file_id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("versions", response.json())

    def test_a_stranger_still_cannot_touch_the_lock(self):
        """The widening is the bridge's answer, not an open door."""
        self._write(self.member_user)
        self.page.refresh_from_db()

        self.client.force_login(self.stranger_user)
        self.assertEqual(
            self.client.post(
                reverse("vault:lock_acquire", args=[self.page.vault_file_id])
            ).status_code, 404)
        self.assertEqual(
            self.client.get(
                reverse("vault:version_list", args=[self.page.vault_file_id])
            ).status_code, 404)

    def test_the_writer_and_the_lock_agree_about_who_may_edit(self):
        """One decision, asked twice — the drift this refactor removes.

        Whatever `bridge.may_edit` says must be what BOTH the writer and the
        vault's lock endpoints do, for every actor in the fixture. Asserting
        the agreement rather than each verdict is what keeps a later change to
        one path from quietly disagreeing with the other.
        """
        from toto.cyprian.bridge import may_edit

        self._write(self.member_user)
        self.page.refresh_from_db()
        vault_file = self.page.vault_file

        for user in (self.lead_user, self.member_user, self.reader_user,
                     self.stranger_user):
            with self.subTest(user=user.get_username()):
                allowed = may_edit(user, vault_file)
                self.client.force_login(user)
                writer = self.client.get(
                    reverse("cyprian:edit", args=[vault_file.pk]))
                lock = self.client.post(
                    reverse("vault:lock_acquire", args=[vault_file.pk]))
                self.client.post(
                    reverse("vault:lock_release", args=[vault_file.pk]))
                # The lead reaches the writer as OWNER, so may_edit is true for
                # them by the ownership arm rather than the bridge; every other
                # actor's two answers must match the helper exactly.
                self.assertEqual(writer.status_code == 200, allowed)
                self.assertEqual(lock.status_code == 200, allowed)

    def test_source_stays_owner_only_even_with_a_bridge(self):
        """Raw XML, no renderer, no sanitiser — deliberately not widened.

        `document_source` is the cleanest exfiltration primitive cyprian has, and
        a shared wiki page does not need it. If someone bridges it later, this
        test is the argument they have to answer.
        """
        self._write(self.member_user)
        self.page.refresh_from_db()

        self.client.force_login(self.member_user)
        self.assertEqual(
            self.client.get(
                reverse("cyprian:source", args=[self.page.vault_file_id])
            ).status_code, 404)

    def test_forged_meta_does_not_hand_a_page_someone_elses_file(self):
        """The attack the column-backed claim exists to stop.

        `meta` is writable by anyone who can save a document, so a bridge that
        trusted it would let a member stamp `kanban_page` onto a file they own
        and have the team authorised onto it — or worse, claim a page whose
        prose then renders from a file the page never pointed at.
        """
        from django.core.files.base import ContentFile
        from toto.cyprian import document_format
        from toto.cyprian.bridge import DocumentBridge

        bucket = Bucket.objects.create(name="S", slug="s",
                                       owner=self.stranger_user,
                                       storage_backend="local")
        document = document_format.new_document("Forged")
        document.meta["kanban_page"] = str(self.page.pk)
        forged = VaultFile(owner=self.stranger_user, title="forged.xml",
                           key="forged", file_type="document", bucket=bucket)
        forged.save()
        forged.file.save("forged.xml",
                         ContentFile(document_format.dumps(document).encode()),
                         save=True)

        # No page points at this file, so nothing claims it — the meta is a
        # breadcrumb and never a permission.
        self.assertIsNone(DocumentBridge.for_file(forged, document))

        self.client.force_login(self.member_user)
        self.assertEqual(
            self.client.get(reverse("cyprian:edit", args=[forged.pk])).status_code,
            404)


class MissionPageTests(WikiWorld):
    def test_the_mission_page_lists_its_wiki_pages(self):
        """The regression that would 500 studio and aurelian.

        `select_related("documentation_page")` became a FieldError and
        `documentation_page.RelatedObjectDoesNotExist` an AttributeError the
        moment the one-to-one became a foreign key. No smoke path on any host
        renders a mission, so this test is the only thing that catches it.
        """
        from toto.api.testutils import add_to_mesh

        add_to_mesh(self.member_user)
        self.client.force_login(self.member_user)
        response = self.client.get(self.url("mission_detail", self.mission.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Getting started")


class MigrationFoldTests(TestCase):
    """The data step in 0005, exercised directly.

    Not through the migration executor: 0005 raises on reverse by design, so
    there is no way to stand a test database back up at 0004 and walk it
    forward. The schema half is covered anyway — every test in this file runs
    against a database built by applying 0001..0005 in order — so what is left
    worth asserting is the fold, which is the part that moves prose and the part
    that would silently lose it.

    Stubs stand in for the historical models, which is what `apps.get_model`
    hands the real function.
    """

    class _Section:
        def __init__(self, page_id, title, content, order, pk):
            self.page_id, self.title, self.content = page_id, title, content
            self.order, self.pk = order, pk

    class _Page:
        def __init__(self, pk):
            self.pk, self.body_html, self.saved = pk, "", False

        def save(self, update_fields=None):
            self.saved = True

    def _run(self, pages, sections):
        import importlib

        # import_module, not a from-import: the module name starts with a digit.
        module = importlib.import_module(
            "toto.kanban.migrations.0005_project_wiki")

        class _Manager:
            def __init__(self, rows):
                self._rows = rows

            def order_by(self, *args):
                return self

            def iterator(self):
                return iter(self._rows)

        class _Apps:
            def get_model(self, app_label, name):
                rows = pages if name == "DocumentationPage" else sections
                return type("M", (), {"objects": _Manager(rows)})

        module.fold_sections_into_body(_Apps(), None)

    def test_titles_become_headings_in_order(self):
        page = self._Page(1)
        self._run([page], [
            self._Section(1, "Scope", "<p>a</p>", 1, 10),
            self._Section(1, "Criteria", "<ul><li>b</li></ul>", 2, 11),
        ])
        self.assertEqual(
            page.body_html,
            "<h2>Scope</h2>\n<p>a</p>\n<h2>Criteria</h2>\n<ul><li>b</li></ul>")

    def test_a_section_title_is_escaped(self):
        """A CharField has never been HTML; promoting it must not make it so."""
        page = self._Page(1)
        self._run([page], [self._Section(1, "A < B", "<p>x</p>", 1, 10)])
        self.assertIn("&lt;", page.body_html)
        self.assertNotIn("<h2>A < B</h2>", page.body_html)

    def test_an_untitled_section_contributes_only_its_body(self):
        page = self._Page(1)
        self._run([page], [self._Section(1, "", "<p>just prose</p>", 1, 10)])
        self.assertEqual(page.body_html, "<p>just prose</p>")

    def test_a_page_with_no_sections_is_left_alone(self):
        page = self._Page(1)
        self._run([page], [])
        self.assertEqual(page.body_html, "")
        self.assertFalse(page.saved)

    def test_sections_of_other_pages_do_not_leak_in(self):
        first, second = self._Page(1), self._Page(2)
        self._run([first, second], [
            self._Section(1, "Mine", "<p>mine</p>", 1, 10),
            self._Section(2, "Theirs", "<p>theirs</p>", 1, 11),
        ])
        self.assertIn("mine", first.body_html)
        self.assertNotIn("theirs", first.body_html)
        self.assertIn("theirs", second.body_html)


class IngressTests(TestCase):
    """The demo seeder, which studio's gate runs on every deploy.

    Its pages used to be keyed on `slug` alone. That was fine while the slug was
    globally unique and is a MultipleObjectsReturned waiting to happen now that
    two projects may both hold "mvp-launch-overview" — on the second project
    anybody seeds, which is exactly what `init_data` does on a host with real
    data already in it.
    """

    def test_full_ingress_seeds_pages_and_can_run_twice(self):
        from django.core.management import call_command

        for name in ("alpha", "beta"):
            _person(name)

        call_command("ingress_kanban", full=True)
        seeded = DocumentationPage.objects.count()
        self.assertEqual(seeded, 2)
        # Seeded as body_html, not as section rows — that is what every host
        # renders, including the ones with no writer to open the page in.
        self.assertTrue(
            DocumentationPage.objects
            .filter(slug="mvp-launch-overview")
            .exclude(body_html="")
            .exists())

        call_command("ingress_kanban", full=True)
        self.assertGreaterEqual(DocumentationPage.objects.count(), seeded)
