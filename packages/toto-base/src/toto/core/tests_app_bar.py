"""The app bar, terser (2026-10-02, stage 50). The owner: "dont say "my
profile" in app bar just profile, dont say light dark mode just icon of sun
or moon", and "also the lang dropdown say ENG or PL".

``oya/header.html`` signed in, in both its bars — the desktop one and the
phone menu: ONE entry for the member's own page (My account and the profile
became one page), reading Profile, and no "My account" or "My Profile"; the
theme switch is the sun or the moon alone, with no words on screen, named for
screen readers by its title and aria-label (what a click does); and the
language select offers ENG and PL — any other language its code in capitals
— the current one selected, the select itself named "Language".

The header's nav items come from the host's ``HEADER_NAV_ITEMS``; the host
checks its own (zenobia's ``tests/test_app_bar.py``).

    manage.py test toto.core.tests_app_bar
"""

from __future__ import annotations

from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from toto.core.models import Platform

#: Elements that have no end tag.
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr"}


class _Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children, self.text = [], []

    def all(self):
        for child in self.children:
            yield child
            yield from child.all()

    def words(self):
        parts = self.text + [child.words() for child in self.children]
        return " ".join(" ".join(parts).split())

    def classes(self):
        return (self.attrs.get("class") or "").split()


class _Tree(HTMLParser):
    """Just enough of the page's tree to ask what each control says."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", [], None)
        self._at = self.root
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self._at)
        self._at.children.append(node)
        if tag not in VOID:
            self._at = node

    def handle_startendtag(self, tag, attrs):
        self._at.children.append(_Node(tag, attrs, self._at))

    def handle_endtag(self, tag):
        node = self._at
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self._at = node.parent

    def handle_data(self, data):
        self._at.text.append(data)

    def find(self, tag, test=lambda node: True):
        return [node for node in self.root.all() if node.tag == tag and test(node)]


class AppBarTests(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.user = get_user_model().objects.create_user("ada", password="x")

    def bar(self, user=None, language="en"):
        request = RequestFactory().get("/somewhere/")
        request.user = user or self.user
        with translation.override(language):
            html = render_to_string("oya/header.html", {"header_nav_items": []},
                                    request=request)
        return html, _Tree(html)

    def test_one_profile_entry_in_each_bar(self):
        html, tree = self.bar()
        entries = tree.find("a", lambda a: a.attrs.get("href") == reverse("account:home"))
        self.assertEqual(sorted(a.attrs.get("id") for a in entries),
                         ["header-profile", "header-profile-mobile"])
        for entry in entries:
            with self.subTest(entry=entry.attrs["id"]):
                self.assertEqual(entry.words(), "Profile")
                icons = [n for n in entry.all() if n.tag == "i"]
                self.assertEqual(len(icons), 1)
                self.assertIn("fa-user", icons[0].classes())
                self.assertEqual(icons[0].attrs.get("aria-hidden"), "true")
        for gone in ("My account", "My Profile", "header-my-account", "fa-user-gear"):
            self.assertNotIn(gone, html)

    def test_the_entry_reads_profile_in_polish_too(self):
        _, tree = self.bar(language="pl")
        entries = tree.find("a", lambda a: a.attrs.get("href") == reverse("account:home"))
        with translation.override("pl"):
            self.assertEqual([a.words() for a in entries], [translation.gettext("Profile")] * 2)

    def test_signed_out_there_is_no_profile_entry(self):
        html, tree = self.bar(user=AnonymousUser())
        self.assertEqual(tree.find("a", lambda a: a.attrs.get("href") == reverse("account:home")), [])
        self.assertIn(reverse("sso:login"), html)

    def test_the_theme_switch_is_the_sun_or_the_moon_alone(self):
        html, tree = self.bar()
        switches = tree.find("button", lambda b: "site-theme-toggle-button" in b.classes())
        self.assertEqual(len(switches), 2)
        for switch in switches:
            with self.subTest(switch=switch.attrs.get("class")):
                # No words on screen...
                self.assertEqual(switch.words(), "")
                self.assertEqual([n.tag for n in switch.all()], ["i"])
                icon = switch.children[0]
                self.assertEqual(icon.attrs.get(":class"), "darkMode ? 'fas fa-moon' : 'fas fa-sun'")
                self.assertEqual(icon.attrs.get("aria-hidden"), "true")
                # ...but a name for screen readers and a tooltip: what a click
                # does, from the data- attributes once Alpine runs.
                self.assertEqual(switch.attrs.get("type"), "button")
                self.assertEqual(switch.attrs.get("aria-label"), "Switch to dark mode")
                self.assertEqual(switch.attrs.get("title"), "Switch to dark mode")
                self.assertEqual(switch.attrs.get("data-to-dark"), "Switch to dark mode")
                self.assertEqual(switch.attrs.get("data-to-light"), "Switch to light mode")
                for bound in (":aria-label", ":title"):
                    self.assertEqual(switch.attrs.get(bound),
                                     "darkMode ? $el.dataset.toLight : $el.dataset.toDark")
                # The graph pages hook its click.
                self.assertIn("darkMode = !darkMode", switch.attrs.get("@click", ""))
        for gone in ("Light Mode", "Dark Mode", "x-text"):
            self.assertNotIn(gone, html)

    def test_the_language_select_says_eng_or_pl(self):
        for language in ("en", "pl"):
            with self.subTest(language=language):
                _, tree = self.bar(language=language)
                selects = tree.find("select", lambda s: s.attrs.get("name") == "language")
                self.assertEqual(len(selects), 2)
                with translation.override(language):
                    name = translation.gettext("Language")
                for select in selects:
                    self.assertEqual(select.attrs.get("aria-label"), name)
                    self.assertEqual(select.attrs.get("title"), name)
                    self.assertEqual(select.attrs.get("onchange"), "this.form.submit()")
                    options = [n for n in select.all() if n.tag == "option"]
                    self.assertEqual([(o.attrs["value"], o.words()) for o in options],
                                     [("en", "ENG"), ("pl", "PL")])
                    chosen = [o.attrs["value"] for o in options if "selected" in o.attrs]
                    self.assertEqual(chosen, [language])
                    # The language's own name stays, as the option's title.
                    self.assertTrue(all(o.attrs.get("title") for o in options))

    @override_settings(LANGUAGES=[("en", "English"), ("pl", "Polski"), ("pt-br", "Português")])
    def test_another_language_shows_its_code(self):
        _, tree = self.bar()
        select = tree.find("select", lambda s: s.attrs.get("name") == "language")[0]
        self.assertEqual([n.words() for n in select.all() if n.tag == "option"],
                         ["ENG", "PL", "PT-BR"])
