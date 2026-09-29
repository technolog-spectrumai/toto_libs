"""The read page — a deck as a page.

These are regression tests for a page that rendered but was broken in a way no
status code showed: it was the one memo view that skipped `PageProcessor`, so
`oya/base.html` wrote its Tailwind config as `colors: }` — a JS syntax error
that took the site palette down with it and left the page ignoring light/dark
entirely. A 200 meant nothing here, so these assert on what the page actually
emits.
"""
import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from toto.core.models import ColorMix, Font, Platform, Theme
from toto.vault.models import Bucket, VaultFile

from . import presentation_format

User = get_user_model()

_MEDIA = tempfile.mkdtemp(prefix="memo-read-")


@override_settings(MEDIA_ROOT=_MEDIA)
class PresentationReadTests(TestCase):
    def setUp(self):
        # A THEMED platform, because an unthemed one is not the shape any host
        # actually runs — and the page's palette is exactly what is under test.
        theme = Theme.objects.create(
            name="T",
            color_mix=ColorMix.objects.create(name="M"),
            font=Font.objects.create(name="Roboto", style_family="sans-serif"),
        )
        Platform.objects.create(
            site_name="Test", author="Test", publication_year=2026, active=True,
            theme=theme,
        )
        self.user = User.objects.create_user("reader", password="pw")
        self.other = User.objects.create_user("stranger", password="pw")
        self.bucket = Bucket.objects.create(
            owner=self.user, name="B", slug="b", storage_backend="local")
        self.deck = self._deck(theme="white", font="serif")

    def _deck(self, *, theme, font, file_type="pxml", key="deck-1",
              name="deck.pxml"):
        presentation = presentation_format.new_presentation(
            title="Quarterly", theme=theme, font=font)
        vault_file = VaultFile.objects.create(
            owner=self.user, bucket=self.bucket, title=name,
            key=key, file_type=file_type)
        vault_file.file.save(
            name,
            ContentFile(presentation_format.dumps(presentation).encode()),
            save=True)
        return vault_file

    def read(self, vault_file=None):
        return self.client.get(
            reverse("memo:read", args=[(vault_file or self.deck).pk]))

    # ── the regression ───────────────────────────────────────────────────────

    def test_the_page_carries_a_theme(self):
        # The root cause: this view rendered without PageProcessor, so there
        # was no theme to write into the page at all.
        self.client.force_login(self.user)
        self.assertIsNotNone(self.read().context["theme"])

    def test_the_tailwind_config_is_not_left_with_an_empty_colors_value(self):
        # The symptom the browser reported: `colors: }` is a syntax error, and
        # it kills the whole config — palette and dark mode with it.
        self.client.force_login(self.user)
        body = self.read().content.decode()
        self.assertNotIn("colors: }", body)
        self.assertNotIn("colors: \n", body)

    def test_the_page_reacts_to_light_and_dark(self):
        self.client.force_login(self.user)
        self.assertContains(self.read(), "darkMode ?")

    def test_no_template_comment_leaks_onto_the_page(self):
        # Django's `{#` comment is SINGLE-LINE. Spanning lines with it does not
        # comment anything out — it prints the comment to the reader, which is
        # how a note about the Tailwind config ended up rendered into the page.
        self.client.force_login(self.user)
        body = self.read().content.decode()
        self.assertNotIn("{#", body)
        self.assertNotIn("#}", body)

    def test_the_slide_stylesheet_is_linked(self):
        # It was declared in a block oya/base.html does not define, so it was
        # never rendered — and without it a slide has no layout at all.
        self.client.force_login(self.user)
        self.assertContains(self.read(), "memo/slide.css")

    def test_a_slide_keeps_the_theme_and_font_the_deck_chose(self):
        # The include passed the whole presentation, but _slide.html reads flat
        # `theme` and `font` — so every deck rendered as the default black/sans
        # no matter what its author picked.
        self.client.force_login(self.user)
        body = self.read().content.decode()
        self.assertIn("memo-theme-white", body)
        self.assertIn("memo-font-serif", body)

    def test_slides_are_scaled_to_the_column(self):
        # A slide is a fixed 1280x720 box; dropped in raw it overflows the page.
        self.client.force_login(self.user)
        self.assertContains(self.read(), "memo-read-slide")

    # ── access ───────────────────────────────────────────────────────────────

    def test_it_needs_a_login(self):
        self.assertNotEqual(self.read().status_code, 200)

    def test_somebody_elses_deck_is_not_readable(self):
        self.client.force_login(self.other)
        self.assertEqual(self.read().status_code, 404)

    def test_the_owner_can_read_their_own_deck(self):
        self.client.force_login(self.user)
        self.assertEqual(self.read().status_code, 200)

    # ── the file class ───────────────────────────────────────────────────────

    def test_a_pxml_file_types_itself(self):
        # The whole point of the extension: no ingest door has to read a deck
        # to find out that it is one.
        self.assertEqual(VaultFile.detect_type("", "talk.pxml"), "pxml")
        self.assertEqual(VaultFile.detect_type("application/xml", "talk.pxml"), "pxml")
        # And a cyprian document is still an ordinary xml file.
        self.assertEqual(VaultFile.detect_type("", "notes.xml"), "xml")

    def test_pxml_is_a_real_file_class(self):
        self.assertIn("pxml", dict(VaultFile.FILE_TYPES))

    def test_a_deck_still_typed_with_the_legacy_spelling_opens(self):
        # Migration 0021 cannot reach mirrored, remote or encrypted rows, so
        # the old string has to keep working — a deck it missed should be dull,
        # not missing.
        legacy = self._deck(theme="black", font="sans", file_type="presentation",
                            key="legacy-deck", name="legacy.xml")
        self.client.force_login(self.user)
        self.assertEqual(self.read(legacy).status_code, 200)

    def test_an_xml_file_holding_deck_bytes_is_not_a_deck(self):
        # The sniff is gone: type is the whole answer. This is the deliberate
        # cost of not reading 300 files on every listing.
        sniffable = self._deck(theme="black", font="sans", file_type="xml",
                               key="sniffable", name="looks-like-one.xml")
        self.client.force_login(self.user)
        self.assertEqual(self.read(sniffable).status_code, 404)

    def test_the_gallery_lists_both_spellings_and_reads_no_files(self):
        legacy = self._deck(theme="black", font="sans", file_type="presentation",
                            key="legacy-deck", name="legacy.xml")
        self.client.force_login(self.user)
        # The gallery kept its cover-rendering job through the Office era
        # (/memo/ was that hub's Presentations tab until 2026-09-02) and is
        # the index again now.
        response = self.client.get(reverse("memo:gallery"))
        listed = {row["file_pk"] for row in response.context["presentations"]}
        self.assertEqual(listed, {self.deck.pk, legacy.pk})

    # ── what is left on the page ─────────────────────────────────────────────

    def test_the_edit_link_follows_the_urlconf_the_host_mounted(self):
        """Asserted BOTH ways, because both are real deployments.

        This demanded an unconditional edit link until 2026-09-05, when
        `reader_urls` arrived: a host may mount only the reading half of this
        app, and then `memo:edit` is not registered. `views._maybe_reverse`
        answers "" there, and the template treats "" as "do not offer the
        link" — so the reading page still renders, which is the whole point of
        such a mount.

        Deciding which case to assert by asking the urlconf, rather than
        picking one, is what makes this test true on both hosts without going
        vacuous on either: exactly one branch runs, and each one asserts
        something.
        """
        self.client.force_login(self.user)
        response = self.read()
        try:
            edit_url = reverse("memo:edit", args=[self.deck.pk])
        except NoReverseMatch:
            # Reading-only host. The link is absent and the page is fine —
            # `PresentationReadView` filters on `owner=request.user`, so this
            # is not a permission question, it is a mount question.
            self.assertEqual(response.context["edit_url"], "")
            self.assertNotContains(response, "/memo/edit/")
            return
        # Full host. Unconditional, because everyone who can load this page
        # owns the deck; whether they may SAVE is the door's question on the
        # editor page.
        self.assertEqual(response.context["edit_url"], edit_url)
        self.assertContains(response, edit_url)

    def test_it_links_to_the_player_and_the_pdf(self):
        self.client.force_login(self.user)
        response = self.read()
        self.assertContains(response, reverse("memo:present", args=[self.deck.pk]))
        self.assertContains(response, reverse("memo:export_pdf", args=[self.deck.pk]))


class DeckClearanceTests(PresentationReadTests):
    """A deck kept to clearances (2026-09-29) is theirs alone: gone from the
    gallery, a 404 in the player, whatever its public flag."""

    def setUp(self):
        super().setUp()
        from toto.people.models import Person
        from toto.socialhub.models import Clearance
        from toto.vault.models import VaultFileClearance

        self.internal = Clearance.objects.create(name="internal", slug="internal")
        self.member = User.objects.create_user("member", password="pw")
        Person.objects.create(user=self.member, display_name="M").clearances.add(self.internal)
        self.deck.is_public = True
        self.deck.save()
        VaultFileClearance.objects.create(file=self.deck, clearance=self.internal)

    def test_the_gallery_and_the_player(self):
        gallery, present = reverse("memo:gallery"), reverse("memo:present", args=[self.deck.pk])
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(gallery), "deck.pxml")
        self.assertEqual(self.client.get(present).status_code, 404)
        self.client.force_login(self.member)
        self.assertContains(self.client.get(gallery), "deck.pxml")
        self.assertEqual(self.client.get(present).status_code, 200)
        self.client.logout()
        # A visitor is sent to log in (this host's login middleware; the
        # player would send them too) — never shown a kept deck.
        for url in (gallery, present):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertNotIn("deck.pxml", response.content.decode())

    def test_the_owner_has_the_door(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("memo:gallery")), 'data-testid="deck-access"')
        self.assertContains(self.client.get(reverse("memo:read", args=[self.deck.pk])),
                            reverse("vault:file_access", args=[self.deck.pk]))
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(reverse("memo:gallery")), 'data-testid="deck-access"')
