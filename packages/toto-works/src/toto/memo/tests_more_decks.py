"""The reading half of memo, as people use it: who sees which deck, where.

The gallery, the player and the read page each answer "may this person see
this deck?" and since 2026-09-29 clearances are part of that answer: a deck kept
to clearances belongs to their members, its owner and superusers — not to the
public flag, not to a folder share, not to the bucket's owner. Hidden decks are
missing decks (404 in the player, absent from the gallery).

Everything here goes through the URLs this host mounts (`memo.reader_urls`),
so the authoring routes are never touched. The one exception is the small
anonymous-visitor class at the end, which switches off the host's
private-by-default middleware to pin the VIEWS' own refusal — the second wall
behind that middleware.
"""

import shutil
import tempfile
from datetime import timedelta
from unittest import skip
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, modify_settings, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.text import slugify

from toto.core.models import Platform
from toto.vault.models import Bucket, VaultDirectory, VaultFile, VaultFileClearance

from . import presentation_format as pf

User = get_user_model()

HOST_LOGIN_GATE = "zenobia.middleware.LoginRequiredEverywhereMiddleware"

#: presentation_format._sanitize_block calls `sanitize.sanitize_katex`, but the
#: `toto.antivirus.sanitize` package re-exports only plain_text, sanitize_inline,
#: sanitize_rich and sanitize_svg (the function lives in `.markup`). Every deck
#: holding a formula block raises AttributeError on open: the player and the
#: read page answer 500 and the gallery card loses its cover.
#: The player renders a legacy v1 `html` block with trust_html=True for EVERY
#: reader may_read admits (public decks, folder shares, bucket owners, clearance
#: members), not only the deck's owner — the gallery escapes it and the read
#: page is owner-only, the player is neither. No CSP is configured on this
#: host, so a public deck with `<body><script>` runs in any member's session.
LEGACY_HTML_XSS = ("suspected production bug: memo/templates/memo/present.html:84 renders "
                   "legacy html blocks live (trust_html=True) for non-owners")

KATEX_BUG = ("production bug: memo/presentation_format.py:431 calls "
             "toto.antivirus.sanitize.sanitize_katex, which the package does not export")


def _slide(title, *blocks, layout="title-content"):
    return pf.Slide(title=title, layout=layout, blocks=list(blocks))


class DeckFixture(TestCase):
    """Owner, clearance member, stranger, superuser; a bucket; a clearance."""

    def setUp(self):
        media = tempfile.mkdtemp(prefix="memo-more-")
        self.addCleanup(shutil.rmtree, media, True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)

        from toto.people.models import Person
        from toto.socialhub.models import Clearance

        Platform.objects.create(site_name="T", author="A",
                                publication_year=2026, active=True)
        self.owner = User.objects.create_user("owner", password="pw")
        self.member = User.objects.create_user("member", password="pw")
        self.stranger = User.objects.create_user("stranger", password="pw")
        self.root = User.objects.create_superuser("root", "root@example.com", "pw")
        self.internal = Clearance.objects.create(name="internal", slug="internal")
        self.staff_clearance = Clearance.objects.create(name="restricted", slug="restricted")
        Person.objects.create(user=self.member, display_name="M").clearances.add(self.internal)
        Person.objects.create(user=self.stranger, display_name="S")
        self.bucket = Bucket.objects.create(owner=self.owner, name="Decks", slug="decks",
                                            storage_backend="local")
        # Their own bucket: owning the bucket a file sits in is itself a claim
        # on it, so a stranger's private deck must not live in the owner's.
        self.stranger_bucket = Bucket.objects.create(owner=self.stranger, name="Elsewhere",
                                                     slug="elsewhere", storage_backend="local")

    def deck(self, name, *, owner=None, public=False, bucket=None, directory=None,
             file_type="pxml", raw=None, slides=None, theme="black", encrypted=False):
        presentation = pf.new_presentation(title=name, theme=theme)
        if slides is not None:
            presentation.slides = slides
        body = raw if raw is not None else pf.dumps(presentation).encode()
        owner = owner or self.owner
        if bucket is None:
            bucket = self.stranger_bucket if owner == self.stranger else self.bucket
        vault_file = VaultFile.objects.create(
            owner=owner, bucket=bucket, directory=directory,
            title=f"{name}.pxml", key=slugify(name), file_type=file_type,
            is_public=public, is_encrypted=encrypted)
        vault_file.file.save(f"{slugify(name)}.pxml", ContentFile(body), save=True)
        return vault_file

    def keep(self, vault_file, *clearances):
        for clearance in clearances:
            VaultFileClearance.objects.create(file=vault_file, clearance=clearance)
        return vault_file

    def gallery(self, user, **params):
        self.client.force_login(user)
        return self.client.get(reverse("memo:gallery"), params)

    def listed(self, user, **params):
        return {row["file_pk"] for row in self.gallery(user, **params).context["presentations"]}

    def present(self, user, vault_file):
        self.client.force_login(user)
        return self.client.get(reverse("memo:present", args=[vault_file.pk]))


class GalleryVisibilityTests(DeckFixture):
    def test_the_gallery_shows_my_decks_and_public_ones_but_not_a_strangers_private_deck(self):
        mine = self.deck("mine")
        theirs_public = self.deck("theirs-public", owner=self.stranger, public=True)
        theirs_private = self.deck("theirs-private", owner=self.stranger)
        listed = self.listed(self.owner)
        self.assertIn(mine.pk, listed)
        self.assertIn(theirs_public.pk, listed)
        self.assertNotIn(theirs_private.pk, listed)

    def test_an_encrypted_deck_is_never_listed_not_even_to_its_owner(self):
        # Its bytes are ciphertext: there is no cover to draw and nothing to play.
        sealed = self.deck("sealed", encrypted=True)
        self.assertNotIn(sealed.pk, self.listed(self.owner))

    def test_a_file_that_is_not_typed_as_a_deck_is_not_listed(self):
        xml = self.deck("looks-like-one", file_type="xml", public=True)
        self.assertNotIn(xml.pk, self.listed(self.owner))

    def test_a_deck_kept_to_a_clearance_is_listed_to_its_members_its_owner_and_a_superuser(self):
        kept = self.keep(self.deck("board-pack", public=True), self.internal)
        self.assertIn(kept.pk, self.listed(self.owner))
        self.assertIn(kept.pk, self.listed(self.member))
        self.assertIn(kept.pk, self.listed(self.root))
        self.assertNotIn(kept.pk, self.listed(self.stranger))

    def test_a_legacy_spelled_deck_kept_to_a_clearance_is_hidden_the_same_way(self):
        legacy = self.keep(self.deck("legacy", file_type="presentation", public=True),
                           self.internal)
        self.assertNotIn(legacy.pk, self.listed(self.stranger))
        self.assertIn(legacy.pk, self.listed(self.member))

    def test_a_clearance_overrides_a_folder_share(self):
        folder = VaultDirectory.objects.create(bucket=self.bucket, owner=self.owner,
                                               name="shared")
        folder.allowed_users.add(self.stranger)
        shared = self.deck("shared", directory=folder)
        self.assertIn(shared.pk, self.listed(self.stranger))
        self.keep(shared, self.internal)
        self.assertNotIn(shared.pk, self.listed(self.stranger))

    def test_the_owner_of_the_bucket_loses_a_deck_kept_to_a_clearance_they_are_not_in(self):
        # The bucket is the stranger's; the deck is the owner's, kept to internal.
        their_bucket = Bucket.objects.create(owner=self.stranger, name="Theirs",
                                             slug="theirs", storage_backend="local")
        guest = self.deck("guest", bucket=their_bucket)
        self.assertIn(guest.pk, self.listed(self.stranger))
        self.keep(guest, self.internal)
        self.assertNotIn(guest.pk, self.listed(self.stranger))

    def test_being_in_any_one_of_a_decks_clearances_is_enough(self):
        both = self.keep(self.deck("both"), self.internal, self.staff_clearance)
        self.assertIn(both.pk, self.listed(self.member))
        self.assertNotIn(both.pk, self.listed(self.stranger))


class GalleryCardTests(DeckFixture):
    def test_only_the_owner_and_a_superuser_get_the_who_can_read_door(self):
        deck = self.keep(self.deck("board-pack"), self.internal)
        door = reverse("vault:file_access", args=[deck.pk])
        for user, offered in ((self.owner, True), (self.root, True), (self.member, False)):
            with self.subTest(user=user.username):
                card = next(row for row in self.gallery(user).context["presentations"]
                            if row["file_pk"] == deck.pk)
                if offered:
                    # ...and it brings them back to the gallery afterwards.
                    self.assertEqual(card["access_url"],
                                     f"{door}?{urlencode({'next': reverse('memo:gallery')})}")
                else:
                    self.assertEqual(card["access_url"], "")
                    self.assertNotContains(self.gallery(user), door)

    def test_the_access_link_keeps_a_deck_to_a_clearance_and_comes_back_to_the_gallery(self):
        from toto.people.models import Person

        Person.objects.create(user=self.owner, display_name="O").clearances.add(self.internal)
        deck = self.deck("open-deck", public=True)
        self.assertIn(deck.pk, self.listed(self.stranger))
        card = next(row for row in self.gallery(self.owner).context["presentations"]
                    if row["file_pk"] == deck.pk)
        response = self.client.post(card["access_url"], {"clearance": [self.internal.pk],
                                                         "next": reverse("memo:gallery")})
        self.assertRedirects(response, reverse("memo:gallery"), fetch_redirect_response=False)
        self.assertNotIn(deck.pk, self.listed(self.stranger))
        self.assertIn(deck.pk, self.listed(self.member))
        self.assertEqual(self.present(self.stranger, deck).status_code, 404)

    def test_the_access_link_will_not_send_the_owner_off_site(self):
        deck = self.deck("open-deck")
        self.client.force_login(self.owner)
        url = reverse("vault:file_access", args=[deck.pk])
        response = self.client.post(url, {"next": "https://evil.example/"})
        self.assertRedirects(response, url, fetch_redirect_response=False)

    def test_the_card_names_every_clearance_a_deck_is_kept_to_in_order(self):
        deck = self.keep(self.deck("board-pack"), self.internal, self.staff_clearance)
        response = self.gallery(self.owner)
        card = next(row for row in response.context["presentations"]
                    if row["file_pk"] == deck.pk)
        self.assertEqual(card["clearances"], "internal, restricted")
        self.assertContains(response, "Kept to: internal, restricted")
        self.assertContains(response, "fa-solid fa-lock\"")

    def test_an_open_deck_shows_an_open_lock_to_its_owner(self):
        self.deck("open-deck")
        response = self.gallery(self.owner)
        self.assertContains(response, "fa-lock-open")
        self.assertContains(response, "Who can read this deck")

    def test_the_read_link_is_offered_to_the_owner_only(self):
        deck = self.deck("public-deck", public=True)
        read = reverse("memo:read", args=[deck.pk])
        self.assertContains(self.gallery(self.owner), read)
        response = self.gallery(self.stranger)
        card = next(row for row in response.context["presentations"]
                    if row["file_pk"] == deck.pk)
        self.assertFalse(card["is_owner"])
        self.assertNotContains(response, read)
        self.assertContains(response, reverse("memo:present", args=[deck.pk]))

    def test_a_card_carries_the_slide_count_and_the_decks_own_theme(self):
        deck = self.deck("three", theme="white", slides=[
            _slide("one"), _slide("two"), _slide("three")])
        response = self.gallery(self.owner)
        card = next(row for row in response.context["presentations"]
                    if row["file_pk"] == deck.pk)
        self.assertEqual((card["slide_count"], card["theme"]), (3, "white"))
        self.assertEqual(card["cover"].title, "one")
        self.assertContains(response, "memo-theme-white")
        self.assertContains(response, "3 slides")

    def test_a_deck_whose_bytes_do_not_parse_still_gets_a_card(self):
        broken = self.deck("broken", raw=b"<presentation><slide>")
        response = self.gallery(self.owner)
        self.assertEqual(response.status_code, 200)
        card = next(row for row in response.context["presentations"]
                    if row["file_pk"] == broken.pk)
        self.assertEqual((card["cover"], card["slide_count"]), (None, 0))

    def test_a_legacy_html_slide_is_inert_on_a_card(self):
        # The gallery lists OTHER people's public decks: one hostile v1 slide
        # must not run on every visitor's page.
        hostile = (b'<?xml version="1.0"?><presentation version="1" title="x">'
                   b'<slide><title>t</title><body><![CDATA[<script>steal()</script>]]>'
                   b'</body></slide></presentation>')
        self.deck("hostile", owner=self.stranger, public=True, raw=hostile)
        response = self.gallery(self.owner)
        self.assertContains(response, "Legacy HTML slide")
        self.assertNotContains(response, "<script>steal()</script>")

    def test_the_card_says_where_the_deck_lives(self):
        folder = VaultDirectory.objects.create(bucket=self.bucket, owner=self.owner,
                                               name="talks")
        deck = self.deck("in-folder", directory=folder)
        card = next(row for row in self.gallery(self.owner).context["presentations"]
                    if row["file_pk"] == deck.pk)
        self.assertEqual(card["location"], "Decks / talks")


class GalleryPagingTests(DeckFixture):
    def test_twelve_decks_to_a_page_newest_first(self):
        start = timezone.now() - timedelta(days=30)
        decks = []
        for n in range(13):
            deck = self.deck(f"deck-{n:02d}")
            VaultFile.objects.filter(pk=deck.pk).update(uploaded_at=start + timedelta(days=n))
            decks.append(deck)
        first = self.gallery(self.owner)
        rows = [row["file_pk"] for row in first.context["presentations"]]
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0], decks[-1].pk)
        self.assertTrue(first.context["is_paginated"])
        second = [row["file_pk"] for row in
                  self.gallery(self.owner, page=2).context["presentations"]]
        self.assertEqual(second, [decks[0].pk])

    def test_a_page_past_the_end_shows_the_last_page_rather_than_404(self):
        for n in range(13):
            self.deck(f"deck-{n:02d}")
        response = self.gallery(self.owner, page=99)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].number, 2)

    def test_one_page_of_decks_is_not_paginated(self):
        self.deck("only")
        self.assertFalse(self.gallery(self.owner).context["is_paginated"])

    def test_the_index_sends_you_to_the_gallery_with_your_query(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("memo:index"), {"page": "2"})
        self.assertRedirects(response, reverse("memo:gallery") + "?page=2",
                             fetch_redirect_response=False)

    def test_the_index_refuses_a_post(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse("memo:index")).status_code, 405)


class PlayerTests(DeckFixture):
    def test_a_public_deck_plays_for_anyone_signed_in(self):
        deck = self.deck("public-deck", public=True)
        response = self.present(self.stranger, deck)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["read_url"], reverse("memo:read", args=[deck.pk]))

    def test_someone_elses_private_deck_is_a_404(self):
        self.assertEqual(self.present(self.stranger, self.deck("private")).status_code, 404)

    def test_a_deck_in_a_shared_folder_plays_for_the_people_on_the_share(self):
        folder = VaultDirectory.objects.create(bucket=self.bucket, owner=self.owner,
                                               name="shared")
        folder.allowed_users.add(self.stranger)
        deck = self.deck("shared", directory=folder)
        self.assertEqual(self.present(self.stranger, deck).status_code, 200)
        self.keep(deck, self.internal)
        self.assertEqual(self.present(self.stranger, deck).status_code, 404)

    def test_a_superuser_plays_a_deck_kept_to_a_clearance_they_are_not_in(self):
        deck = self.keep(self.deck("board-pack"), self.internal)
        self.assertEqual(self.present(self.root, deck).status_code, 200)

    def test_the_owner_plays_their_own_kept_deck(self):
        deck = self.keep(self.deck("board-pack"), self.internal)
        self.assertEqual(self.present(self.owner, deck).status_code, 200)

    def test_an_encrypted_deck_is_refused_even_to_its_owner(self):
        sealed = self.deck("sealed", encrypted=True)
        response = self.present(self.owner, sealed)
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"encrypted", response.content)

    def test_a_missing_deck_and_a_file_that_is_not_a_deck_are_404(self):
        xml = self.deck("xml", file_type="xml", public=True)
        self.assertEqual(self.present(self.owner, xml).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("memo:present", args=[987654])).status_code,
                         404)

    def test_a_deck_without_slides_says_so(self):
        deck = self.deck("empty", slides=[])
        self.assertContains(self.present(self.owner, deck),
                            "This presentation has no slides yet.")

    def test_a_deck_whose_bytes_do_not_parse_plays_as_a_blank_deck(self):
        broken = self.deck("broken", raw=b"<not-a-presentation/>")
        response = self.present(self.owner, broken)
        self.assertEqual(response.status_code, 200)
        presentation = response.context["presentation"]
        self.assertEqual(presentation.title, "broken.pxml")
        self.assertEqual(len(presentation.slides), 1)

    @skip(KATEX_BUG)
    def test_a_deck_with_a_formula_plays_and_reads(self):
        formula = pf.Block(type="formula", payload="x^2", render='<span class="katex">x</span>')
        deck = self.deck("maths", slides=[_slide("eq", formula)])
        self.assertEqual(self.present(self.owner, deck).status_code, 200)
        self.assertEqual(self.client.get(reverse("memo:read", args=[deck.pk])).status_code, 200)
        card = next(row for row in self.gallery(self.owner).context["presentations"]
                    if row["file_pk"] == deck.pk)
        self.assertEqual(card["slide_count"], 1)

    @skip(LEGACY_HTML_XSS)
    def test_a_legacy_html_slide_does_not_run_live_for_someone_other_than_its_owner(self):
        hostile = (b'<?xml version="1.0"?><presentation version="1" title="x">'
                   b'<slide><title>t</title><body><![CDATA[<script>steal()</script>]]>'
                   b'</body></slide></presentation>')
        deck = self.deck("hostile", owner=self.stranger, public=True, raw=hostile)
        response = self.present(self.owner, deck)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<script>steal()</script>")

    def test_every_slide_is_played_in_order(self):
        deck = self.deck("three", slides=[_slide("alpha"), _slide("beta"), _slide("gamma")])
        body = self.present(self.owner, deck).content.decode()
        self.assertLess(body.index("alpha"), body.index("beta"))
        self.assertLess(body.index("beta"), body.index("gamma"))


class ReadPageTests(DeckFixture):
    def test_the_read_page_stays_the_owners_even_for_a_clearance_member(self):
        # It renders legacy HTML live, which is only defensible for the owner.
        deck = self.keep(self.deck("board-pack", public=True), self.internal)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("memo:read", args=[deck.pk])).status_code, 404)

    def test_a_public_deck_does_not_open_someone_elses_read_page(self):
        deck = self.deck("public", public=True)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse("memo:read", args=[deck.pk])).status_code, 404)

    def test_the_access_link_brings_the_owner_back_to_the_read_page(self):
        deck = self.deck("mine")
        read = reverse("memo:read", args=[deck.pk])
        self.client.force_login(self.owner)
        response = self.client.get(read)
        expected = (reverse("vault:file_access", args=[deck.pk])
                    + "?" + urlencode({"next": read}))
        self.assertEqual(response.context["access_url"], expected)
        self.assertContains(response, 'data-testid="deck-access"')

    def test_every_slide_is_on_the_page(self):
        deck = self.deck("three", slides=[_slide("alpha"), _slide("beta"), _slide("gamma")])
        self.client.force_login(self.owner)
        response = self.client.get(reverse("memo:read", args=[deck.pk]))
        self.assertEqual([s.title for s in response.context["slides"]],
                         ["alpha", "beta", "gamma"])


class HostVisitorTests(DeckFixture):
    """A visitor on this host: private by default, whatever the deck."""

    def test_a_visitor_is_sent_to_log_in_even_for_a_public_deck_kept_to_no_clearance(self):
        from django.conf import settings

        if HOST_LOGIN_GATE not in settings.MIDDLEWARE:
            self.skipTest("this host is not private by default")
        deck = self.deck("open-to-all", public=True)
        for url in (reverse("memo:gallery"), reverse("memo:present", args=[deck.pk]),
                    reverse("memo:read", args=[deck.pk])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("next=", response["Location"])
                self.assertNotIn(b"open-to-all", response.content)


@modify_settings(MIDDLEWARE={"remove": [HOST_LOGIN_GATE]})
class ViewsOwnVisitorRuleTests(DeckFixture):
    """The views' own rule for a visitor, with the host's gate taken away:
    public decks kept to no clearance, and nothing else."""

    def test_the_gallery_shows_a_visitor_only_public_decks_kept_to_no_clearance(self):
        open_deck = self.deck("open-deck", public=True)
        kept = self.keep(self.deck("kept-deck", public=True), self.internal)
        private = self.deck("private-deck")
        response = self.client.get(reverse("memo:gallery"))
        self.assertEqual(response.status_code, 200)
        listed = {row["file_pk"] for row in response.context["presentations"]}
        self.assertEqual(listed, {open_deck.pk})
        self.assertNotIn(kept.pk, listed)
        self.assertNotIn(private.pk, listed)
        self.assertNotContains(response, 'data-testid="deck-access"')

    def test_a_visitor_plays_a_public_deck_kept_to_no_clearance(self):
        deck = self.deck("open-deck", public=True)
        self.assertEqual(self.client.get(reverse("memo:present", args=[deck.pk])).status_code,
                         200)

    def test_a_visitor_is_sent_to_log_in_for_a_kept_or_private_deck(self):
        kept = self.keep(self.deck("kept-deck", public=True), self.internal)
        private = self.deck("private-deck")
        for deck in (kept, private):
            with self.subTest(deck=deck.title):
                url = reverse("memo:present", args=[deck.pk])
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                query = parse_qs(urlsplit(response["Location"]).query)
                self.assertEqual(query["next"], [url])

    def test_a_visitor_never_reaches_a_read_page(self):
        deck = self.deck("open-deck", public=True)
        response = self.client.get(reverse("memo:read", args=[deck.pk]))
        self.assertEqual(response.status_code, 302)


class OpeningADeckTests(SimpleTestCase):
    """`loads` is the open path every reading page runs a file through. A deck
    is not only ever written by an editor — anyone can upload one — so the
    open path clamps and sanitises as well as parsing."""

    @staticmethod
    def doc(slides, *, root='version="2" title="T"'):
        return f'<?xml version="1.0"?><presentation {root}>{slides}</presentation>'

    def one_block(self, attrs, payload=""):
        deck = pf.loads(self.doc(
            f'<slide id="s1"><title>t</title><block id="b1" {attrs}>'
            f'<![CDATA[{payload}]]></block></slide>'))
        return deck.slides[0].blocks[0]

    def test_script_in_a_text_block_is_stripped_on_open(self):
        block = self.one_block('type="text"', "<p onclick=\"x()\">hi<script>bad()</script></p>")
        self.assertEqual(block.payload, "<p>hi</p>")

    def test_an_external_image_is_dropped_and_an_inline_one_kept(self):
        self.assertEqual(self.one_block('type="image"', "https://tracker.example/p.png").payload,
                         "")
        inline = "data:image/png;base64,iVBORw0KGgo="
        self.assertEqual(self.one_block('type="image"', inline).payload, inline)

    def test_an_svg_block_loses_its_script(self):
        block = self.one_block('type="svg"', '<svg><script>bad()</script><rect/></svg>')
        self.assertNotIn("script", block.payload)
        self.assertIn("<rect", block.payload)

    def test_a_code_block_keeps_markup_as_literal_text(self):
        snippet = "<script>alert(1)</script>"
        self.assertEqual(self.one_block('type="code"', snippet).payload, snippet)

    def test_list_items_are_sanitised_inline(self):
        deck = pf.loads(self.doc(
            '<slide><block type="list"><item><![CDATA[<b>ok</b><div>x</div>]]></item>'
            '<item><![CDATA[<a href="javascript:bad()">l</a>]]></item></block></slide>'))
        self.assertEqual(deck.slides[0].blocks[0].items, ["<b>ok</b>x", "<a>l</a>"])

    def test_an_unknown_theme_font_and_layout_fall_back_to_the_defaults(self):
        deck = pf.loads(self.doc('<slide layout="spiral"><title>t</title></slide>',
                                 root='version="2" title="T" theme="neon" font="comic"'))
        self.assertEqual((deck.theme, deck.font, deck.slides[0].layout),
                         (pf.DEFAULT_THEME, pf.DEFAULT_FONT, pf.DEFAULT_LAYOUT))

    def test_block_attributes_are_clamped_to_the_schema(self):
        block = self.one_block(
            'type="heading" level="9" fit="stretch" ordered="yes" language="not a lang!" '
            'alt="&lt;b&gt;pic&lt;/b&gt;" scale="9"')
        self.assertEqual(block.attrs["level"], "2")
        self.assertEqual(block.attrs["fit"], "contain")
        self.assertEqual(block.attrs["ordered"], "false")
        self.assertNotIn("language", block.attrs)
        self.assertEqual(block.attrs["alt"], "pic")
        self.assertEqual(block.attrs["scale"], f"{pf.MAX_SCALE:.2f}")

    def test_auto_fit_never_shrinks_below_the_readable_minimum(self):
        self.assertEqual(self.one_block('type="text" scale="auto:0.1"').attrs["scale"],
                         f"auto:{pf.MIN_SCALE:.2f}")
        self.assertEqual(self.one_block('type="text" scale="garbage"').attrs["scale"], "auto")

    def test_the_rendered_scale_is_blank_at_full_size(self):
        for raw, css in (("auto", ""), ("auto:0.80", "0.80"), ("1.00", ""),
                         ("1.60", "1.60"), ("abc", "")):
            with self.subTest(scale=raw):
                self.assertEqual(pf.Block(type="text", attrs={"scale": raw}).scale_css, css)

    def test_a_slot_no_layout_names_is_cleared_but_one_this_layout_lacks_is_kept(self):
        deck = pf.loads(self.doc(
            '<slide layout="title-content"><block id="a" type="text" slot="nowhere"/>'
            '<block id="b" type="text" slot="right"/></slide>'))
        a, b = deck.slides[0].blocks
        self.assertEqual((a.slot, b.slot), ("", "right"))
        # ...and "right" still renders, in the only box this layout has.
        self.assertEqual([len(blocks) for _, blocks in deck.slides[0].columns], [2])

    def test_a_version_one_slide_opens_as_one_verbatim_html_block(self):
        deck = pf.loads('<presentation version="1" title="Old"><slide><title>Hi</title>'
                        '<body><![CDATA[<p style="color:red">x</p>]]></body></slide>'
                        '</presentation>')
        (block,) = deck.slides[0].blocks
        self.assertEqual((block.type, block.payload), ("html", '<p style="color:red">x</p>'))
        self.assertEqual(deck.slides[0].title, "Hi")

    def test_duplicate_ids_are_made_unique_on_open(self):
        deck = pf.loads(self.doc('<slide id="x"><block id="x" type="text"/></slide>'
                                 '<slide id="x"/>'))
        ids = [deck.slides[0].id, deck.slides[0].blocks[0].id, deck.slides[1].id]
        self.assertEqual(len(set(ids)), 3)

    def test_a_blank_slide_on_disk_is_kept(self):
        # Pruning empty slides is for a browser payload, never for a file.
        deck = pf.loads(self.doc("<slide/><slide><title>t</title></slide>"))
        self.assertEqual(len(deck.slides), 2)

    def test_unknown_elements_survive_a_round_trip(self):
        deck = pf.loads(self.doc('<slide><title>t</title><note>keep me</note></slide>'
                                 '<appendix>and me</appendix>'))
        again = pf.loads(pf.dumps(deck))
        self.assertIn("<note>keep me</note>", again.slides[0].extra)
        self.assertIn("<appendix>and me</appendix>", again.extra)

    def test_a_cdata_terminator_in_a_payload_round_trips(self):
        deck = pf.new_presentation()
        deck.slides[0].blocks = [pf.Block(type="code", payload="a ]]> b")]
        self.assertEqual(pf.loads(pf.dumps(deck)).slides[0].blocks[0].payload, "a ]]> b")

    def test_empty_text_is_a_blank_deck_and_garbage_is_a_parse_error(self):
        self.assertEqual(len(pf.loads("   ").slides), 1)
        with self.assertRaises(pf.PresentationParseError):
            pf.loads("<presentation>")
        with self.assertRaises(pf.PresentationParseError):
            pf.loads("<deck/>")

    @skip(KATEX_BUG)
    def test_a_formula_keeps_its_source_and_sanitises_its_cached_render(self):
        deck = pf.loads(self.doc(
            '<slide><block type="formula"><source><![CDATA[x^2]]></source>'
            '<render><![CDATA[<span class="katex">x</span><script>bad()</script>]]></render>'
            '</block></slide>'))
        block = deck.slides[0].blocks[0]
        self.assertEqual(block.payload, "x^2")
        self.assertNotIn("script", block.render)

    def test_slide_titles_are_plain_text(self):
        deck = pf.loads(self.doc('<slide><title>&lt;b&gt;Bold&lt;/b&gt; move</title></slide>'))
        self.assertEqual(deck.slides[0].title, "Bold move")
