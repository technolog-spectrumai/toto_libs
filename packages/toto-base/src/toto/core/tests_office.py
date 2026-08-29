"""Office: the tabs, the lists, the folder panel and the actions it delegates.

The rule these exist to hold: **Office reads and never writes.** The
subscription gate takes the entitlement from `resolver_match.app_name`, and
`office` is not in the catalogue — so an Office-owned write route would let
anybody create Professional content for nothing. Every mutation on the page
posts to the app that owns the file, or to the vault.
"""

from __future__ import annotations

import tempfile

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core import office
from toto.core.models import Platform
from toto.vault.models import Bucket, VaultDirectory, VaultFile

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class OfficeTestCase(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("worker", password="pw")
        self.bucket = Bucket.objects.create(name="Mine", slug="mine",
                                            owner=self.user)
        self.folder = VaultDirectory.objects.create(
            name="Projects", bucket=self.bucket, owner=self.user)
        self.client.force_login(self.user)

    def make(self, key, file_type, *, owner=None, directory=None,
             is_public=False, title=None):
        vault_file = VaultFile(
            owner=owner or self.user, title=title or f"{key}.x", key=key,
            file_type=file_type, bucket=self.bucket, directory=directory,
            is_public=is_public)
        vault_file.file.save(f"{key}.x", SimpleUploadedFile(f"{key}.x", b"hi"),
                             save=True)
        return vault_file

    def section_url(self, slug="documents"):
        return reverse("office:section", args=[slug])

    def open_url(self, file_pk):
        return f"{reverse('office:open')}?file={file_pk}"


class TabTests(OfficeTestCase):
    def test_the_hub_lands_on_the_first_tab(self):
        response = self.client.get(reverse("office:index"))
        self.assertRedirects(response, self.section_url("documents"))

    def test_an_unknown_section_lands_somewhere_real(self):
        """A slug that was a tab on another deployment must not 404 — the
        build decides the tab set, and the reader did not choose the build."""
        response = self.client.get(self.section_url("nonsense"))
        self.assertRedirects(response, self.section_url("documents"))

    def test_every_installed_tab_is_offered(self):
        body = self.client.get(self.section_url()).content.decode()
        for section in office.available_sections():
            self.assertIn(section.label, body)

    def test_a_tab_whose_app_is_absent_is_not_offered(self):
        """Not rendered disabled — not rendered. There is nothing behind it
        to explain, and a dead tab reads as a broken page."""
        absent = office.Section(slug="ghost", label="Ghost", icon="fa-solid fa-ghost",
                                file_types=("nope",),
                                app_labels=("toto.not_installed",))
        self.assertNotIn(absent, office.available_sections())

    def test_login_is_required(self):
        self.client.logout()
        response = self.client.get(self.section_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class ToolTabTests(OfficeTestCase):
    """Tools are tabs, in the same strip, since 2026-08-29.

    They were a row of small buttons off to the right of the tab strip, which
    is where people did not find them. What makes this a real change rather
    than restyling is that ONE builder produces the strip — so a tool cannot
    appear on the Office page and be missing from its own.
    """

    def test_the_strip_holds_sections_and_tools_in_that_order(self):
        tabs = office.office_tabs()
        kinds = [t["is_tool"] for t in tabs]
        self.assertEqual(kinds, sorted(kinds),
                         "a tool was rendered between two sections")
        self.assertTrue(any(t["is_tool"] for t in tabs),
                        "no tool reached the strip at all")

    def test_a_tool_appears_in_the_tab_strip_on_the_office_page(self):
        body = self.client.get(self.section_url()).content.decode()
        for tool in office.available_tools():
            self.assertIn(tool["label"], body)
            self.assertIn(tool["url"], body)

    def test_exactly_one_tab_is_active(self):
        active = [t for t in office.office_tabs(active="documents")
                  if t["active"]]
        self.assertEqual([t["slug"] for t in active], ["documents"])

    def test_a_tool_can_be_the_active_tab(self):
        """The tool's own page lights its tab. Nothing can derive that from
        the request path — the page is in another app entirely — so the slug
        is passed and this is what asserts it works."""
        for tool in office.available_tools():
            with self.subTest(tool=tool["slug"]):
                active = [t["slug"] for t in
                          office.office_tabs(active=tool["slug"])
                          if t["active"]]
                self.assertEqual(active, [tool["slug"]])

    def test_an_absent_tool_is_not_offered(self):
        absent = office.Tool(slug="ghost", label="Ghost",
                             icon="fa-solid fa-ghost", url_name="nowhere:home",
                             app_labels=("toto.not_installed",))
        self.assertNotIn(absent.slug,
                         [t["slug"] for t in office.office_tabs()])

    def test_the_strip_survives_office_being_unmounted(self):
        """`office_tabs` reverses every URL and drops what does not resolve,
        so a host that installs the apps but mounts nothing renders an empty
        strip rather than 500ing every page that includes it."""
        from django.urls import NoReverseMatch
        from unittest import mock

        with mock.patch("django.urls.reverse", side_effect=NoReverseMatch):
            self.assertEqual(office.office_tabs(), [])


class ListingTests(OfficeTestCase):
    def test_each_tab_lists_only_its_own_types(self):
        self.make("doc", "html")
        self.make("deck", "pxml")
        self.make("draw", "svg")
        documents = self.client.get(self.section_url("documents"))
        self.assertEqual(documents.context["total"], 1)
        self.assertEqual(documents.context["rows"][0]["file"].key, "doc")

    def test_both_deck_spellings_are_listed(self):
        """`presentation` is the legacy string vault migration 0021 could not
        reach on mirrored, remote and encrypted rows. A query that lists only
        `pxml` hides those decks from their own owner, silently."""
        self.make("new", "pxml")
        self.make("old", "presentation")
        response = self.client.get(self.section_url("presentations"))
        keys = {row["file"].key for row in response.context["rows"]}
        self.assertEqual(keys, {"new", "old"})

    def test_a_file_you_may_not_read_is_absent(self):
        stranger = User.objects.create_user("stranger", password="pw")
        other_bucket = Bucket.objects.create(name="Theirs", slug="theirs",
                                             owner=stranger)
        hidden = VaultFile(owner=stranger, title="secret.x", key="secret",
                           file_type="html", bucket=other_bucket)
        hidden.file.save("secret.x", SimpleUploadedFile("secret.x", b"hi"),
                         save=True)
        response = self.client.get(self.section_url("documents"))
        self.assertEqual(response.context["total"], 0)

    def test_search_narrows_the_list(self):
        self.make("alpha", "html", title="Quarterly report")
        self.make("beta", "html", title="Handbook")
        response = self.client.get(self.section_url(), {"q": "quarterly"})
        self.assertEqual([r["file"].key for r in response.context["rows"]],
                         ["alpha"])

    def test_sorting_is_honoured_and_garbage_falls_back(self):
        self.make("b", "html", title="Beta")
        self.make("a", "html", title="Alpha")
        by_name = self.client.get(self.section_url(), {"sort": "name"})
        self.assertEqual([r["file"].title for r in by_name.context["rows"]],
                         ["Alpha", "Beta"])
        junk = self.client.get(self.section_url(), {"sort": "; DROP TABLE"})
        self.assertEqual(junk.status_code, 200)
        self.assertEqual(junk.context["sort"], office.DEFAULT_SORT)

    def test_a_folder_filter_narrows_the_list(self):
        self.make("loose", "html")
        self.make("filed", "html", directory=self.folder)
        response = self.client.get(self.section_url(), {"dir": self.folder.pk})
        self.assertEqual([r["file"].key for r in response.context["rows"]],
                         ["filed"])

    def test_a_garbage_folder_id_is_ignored_not_fatal(self):
        self.make("loose", "html")
        response = self.client.get(self.section_url(), {"dir": "banana"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["directory_id"])

    def test_the_empty_states_say_different_things(self):
        """"Nothing matches that" and "Nothing here yet" send the reader to
        different next actions, so they are never the same sentence."""
        empty = self.client.get(self.section_url())
        self.assertContains(empty, "Nothing here yet.")
        self.make("doc", "html", title="Handbook")
        no_match = self.client.get(self.section_url(), {"q": "zzzz"})
        self.assertContains(no_match, "Nothing matches that.")


class OpenTests(OfficeTestCase):
    def test_a_deck_opens_in_its_editor(self):
        deck = self.make("deck", "pxml")
        response = self.client.get(self.open_url(deck.pk))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/memo/", response["Location"])

    def test_a_file_with_no_plugin_falls_back_to_the_vault(self):
        """A tab would otherwise be a list of things that do not open.

        Drawings is the only tab that can have no editor behind it — `svg` is
        an ordinary vault file and no app is required to own it — so on a host
        without toto.sketch the row still opens, through the download door.
        """
        drawing = self.make("draw", "svg")
        response = self.client.get(self.open_url(drawing.pk))
        self.assertEqual(response.status_code, 302)
        if django_apps.is_installed("toto.sketch"):
            self.assertIn("/sketch/edit/", response["Location"])
        else:
            self.assertIn("/vault/", response["Location"])

    def test_the_read_only_note_goes_once_an_editor_claims_the_type(self):
        """The note and an Edit button must never appear together."""
        drawings = office.SECTIONS_BY_SLUG["drawings"]
        self.assertTrue(drawings.read_only_note)
        self.assertEqual(office.type_is_editable(drawings),
                         django_apps.is_installed("toto.sketch"))

    def test_opening_a_file_you_may_not_read_is_a_404(self):
        """404 and not 403, the same reason the download door gives: a 403
        confirms the file exists and turns this into an oracle for other
        people's filenames."""
        stranger = User.objects.create_user("stranger", password="pw")
        other = Bucket.objects.create(name="T", slug="t", owner=stranger)
        hidden = VaultFile(owner=stranger, title="x.x", key="x",
                           file_type="html", bucket=other)
        hidden.file.save("x.x", SimpleUploadedFile("x.x", b"hi"), save=True)
        response = self.client.get(self.open_url(hidden.pk))
        self.assertEqual(response.status_code, 404)

    def test_an_encrypted_file_opens_nowhere(self):
        sealed = self.make("sealed", "html")
        VaultFile.objects.filter(pk=sealed.pk).update(is_encrypted=True)
        sealed.refresh_from_db()
        self.assertEqual(office.open_url(sealed), "")

    def test_a_missing_file_is_a_404(self):
        response = self.client.get(self.open_url(99999))
        self.assertEqual(response.status_code, 404)

    def test_a_garbage_or_absent_file_id_is_a_404_not_a_500(self):
        for query in ("", "?file=", "?file=banana"):
            with self.subTest(query=query):
                response = self.client.get(reverse("office:open") + query)
                self.assertEqual(response.status_code, 404)

    def test_the_tree_links_are_whole_urls(self):
        """`prefix + id` has to be a URL the server answers directly. A path
        prefix would come out as /office/open/1 with no trailing slash and
        lean on APPEND_SLASH to redirect every click."""
        deck = self.make("deck", "pxml")
        response = self.client.get(self.section_url("presentations"))
        self.assertContains(response, f"{reverse('office:open')}?file={deck.pk}")


class DelegationTests(OfficeTestCase):
    """The paywall rule, asserted rather than trusted."""

    def test_no_office_route_accepts_a_write(self):
        self.make("doc", "html")
        for url in (reverse("office:index"), self.section_url("documents"),
                    self.open_url(VaultFile.objects.first().pk)):
            response = self.client.post(url, {})
            self.assertEqual(response.status_code, 405,
                             f"{url} answered a POST with {response.status_code}")

    def test_every_action_on_the_page_posts_to_someone_else(self):
        """Walk the rendered forms: each must aim at the vault or at an app
        that owns the file type, never at /office/."""
        import re

        self.make("doc", "html")
        body = self.client.get(self.section_url()).content.decode()
        actions = re.findall(r'<form[^>]+action="([^"]+)"', body)
        self.assertTrue(actions, "the page rendered no action at all")
        # Not every form on the page is Office's — the shared chrome carries
        # the language switcher. The invariant is about where Office's own
        # actions go, and about what never appears in this list at all.
        for action in actions:
            self.assertFalse(action.startswith("/office/"),
                             f"Office answered its own write: {action}")
        self.assertTrue(any(a.startswith("/vault/") for a in actions),
                        f"no file action was delegated to the vault: {actions}")

    def test_the_new_menu_only_offers_types_a_plugin_can_seed(self):
        """`blank_content` lives on the editor plugin because only the owning
        app knows what an empty workbook or deck looks like. A type without
        one cannot be created here, and no button claims otherwise."""
        drawings = office.SECTIONS_BY_SLUG["drawings"]
        if django_apps.is_installed("toto.sketch"):
            # Sketch declares `new_file_extension = ".svg"`, which is the whole
            # reason the Drawings tab has a New button at all.
            self.assertEqual(office.creatable_types(drawings), [("svg", ".svg")])
        else:
            self.assertEqual(office.creatable_types(drawings), [])
        documents = office.SECTIONS_BY_SLUG["documents"]
        # `html`, and cyprian is not what decides it any more: the writer owns
        # no file type since CTML was retired, so what makes a document
        # creatable here is an EDITOR claiming `html`.
        if django_apps.is_installed("toto.editor"):
            self.assertEqual([t for t, _ext in office.creatable_types(documents)],
                             ["html"])


class FolderPanelTests(OfficeTestCase):
    def test_the_panel_shows_this_tab_s_files(self):
        self.make("filed", "html", directory=self.folder)
        self.make("deck", "pxml")
        tree = self.client.get(self.section_url()).context["tree"]
        titles = [f["title"] for node in tree for group in node["groups"]
                  for f in group["files"]]
        self.assertEqual(titles, ["filed.x"])

    def test_the_panel_carries_a_folder_id_to_link_with(self):
        self.make("filed", "html", directory=self.folder)
        tree = self.client.get(self.section_url()).context["tree"]
        group = next(g for node in tree for g in node["groups"] if g["dir"])
        self.assertEqual(group["dir_id"], self.folder.pk)

    def test_folder_names_render_as_filter_links(self):
        self.make("filed", "html", directory=self.folder)
        response = self.client.get(self.section_url())
        self.assertContains(response, f"?dir={self.folder.pk}")


class AltViewTests(OfficeTestCase):
    def test_presentations_offers_the_cover_gallery(self):
        """memo's gallery renders a real first slide per deck, which Office's
        generic rows cannot do without toto.core importing toto.memo. So it is
        kept and linked rather than replaced."""
        response = self.client.get(self.section_url("presentations"))
        if django_apps.is_installed("toto.memo"):
            self.assertContains(response, reverse("memo:gallery"))
            self.assertContains(response, "Gallery view")

    def test_a_tab_with_no_second_view_offers_none(self):
        response = self.client.get(self.section_url("documents"))
        self.assertEqual(response.context["alt_view_url"], "")


class CreationGateTests(OfficeTestCase):
    def test_no_new_button_without_the_plan(self):
        """primula's index put it best: the form posts to a route the gate
        would 402 anyway, and offering a button that answers "not in your
        plan" is worse than not offering one."""
        from unittest import mock

        with mock.patch("toto.subscriptions.gate.is_entitled", return_value=False):
            response = self.client.get(self.section_url("documents"))
        self.assertFalse(response.context["can_create"])
        self.assertNotContains(response, 'action="/vault/file/create/"')

    def test_the_button_is_there_with_it(self):
        from unittest import mock

        with mock.patch("toto.subscriptions.gate.is_entitled", return_value=True):
            response = self.client.get(self.section_url("documents"))
        if django_apps.is_installed("toto.cyprian"):
            self.assertTrue(response.context["can_create"])

    def test_a_tab_that_needs_no_plan_never_asks(self):
        # Drawings used to be the example here, and stopped being one when
        # sketch came back with an entitlement. The property is about a tab
        # with no entitlement at all, so it is stated with one.
        free = office.Section(slug="free", label="Free", icon="i",
                              file_types=("svg",))
        self.assertTrue(office.may_create(self.user, free))

    def test_a_pdf_is_listed_where_it_was_made(self):
        """Aralia writes a PDF beside the page it rendered. If Documents did
        not list `pdf`, the tab you were looking at would not show the file you
        just made from it."""
        documents = office.SECTIONS_BY_SLUG["documents"]
        self.assertIn("pdf", documents.file_types)

    def test_the_drawings_tab_asks_for_the_drawings_plan(self):
        drawings = office.SECTIONS_BY_SLUG["drawings"]
        self.assertEqual(drawings.entitlement, "sketch")


class HtmlDocumentTests(OfficeTestCase):
    """An HTML page is a document, and Documents is where it lives.

    Before this, the dashboard tile named "Documents" opened the HTML viewer
    while written documents had no listing at all — two things wearing one
    name, and the name led to the wrong one.
    """

    def test_the_documents_tab_holds_both_kinds(self):
        self.make("written", "html", title="Handbook.xml")
        self.make("page", "html", title="Report.html")
        response = self.client.get(self.section_url("documents"))
        keys = {row["file"].key for row in response.context["rows"]}
        self.assertEqual(keys, {"written", "page"})

    def test_a_row_says_which_kind_it_is(self):
        """Only on a tab that mixes kinds — Presentations carries two
        spellings of one kind and would say the same word twice."""
        self.make("page", "html", title="Report.html")
        response = self.client.get(self.section_url("documents"))
        self.assertTrue(response.context["section"].show_type)
        self.assertContains(response, "HTML")
        self.assertFalse(
            office.SECTIONS_BY_SLUG["presentations"].show_type)

    def test_an_html_page_opens_in_the_reader_not_the_converter(self):
        """The whole reason the order is reader-first. `toto.htmlview`
        registers the PLAY plugin for html and renders the page; the EDITOR
        plugin for the same type is cyprian's convert-or-fall-back dispatch,
        and opening a document by converting it would be a strange thing for
        a list to do."""
        page = self.make("page", "html", title="Report.html")
        if django_apps.is_installed("toto.htmlview"):
            self.assertIn("/htmlview/read/", office.open_url(page))

    def test_a_public_page_is_readable_by_all_and_editable_by_its_owner(self):
        """Readable and writable are different questions. The listing must not
        offer a control the door would refuse."""
        page = self.make("page", "html", title="Report.html", is_public=True)
        stranger = User.objects.create_user("stranger", password="pw")
        self.assertTrue(office.open_url(page))
        self.assertEqual(office.edit_url(stranger, page), "")
        self.assertTrue(office.edit_url(self.user, page))

    def test_editing_is_offered_separately(self):
        page = self.make("page", "html", title="Report.html")
        edit = office.edit_url(self.user, page)
        self.assertTrue(edit)
        self.assertNotEqual(edit, office.open_url(page))

    def test_an_encrypted_page_offers_neither(self):
        page = self.make("page", "html", title="Report.html")
        VaultFile.objects.filter(pk=page.pk).update(is_encrypted=True)
        page.refresh_from_db()
        self.assertEqual(office.open_url(page), "")
        self.assertEqual(office.edit_url(self.user, page), "")

    def test_the_old_htmlview_listing_lands_here(self):
        if django_apps.is_installed("toto.htmlview"):
            response = self.client.get(reverse("htmlview:index"))
            self.assertRedirects(response, self.section_url("documents"))
