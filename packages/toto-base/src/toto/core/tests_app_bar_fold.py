"""The app bar folds into its menu below lg (2026-10-12). The owner: "we need
all app bar icons to be able to fold in mobile version into hamburger menu
dropdown. what will stay will be: logo, name, mana, and hamburger on the
right."

``oya/header.html`` read as a tree, signed in, with two throwaway widgets in
the registry (one pinned, one not): what stays on the bar at every width is
the brand, the pinned widgets and the menu button, with the button first, on the left; every other widget, the links, the theme switch and the
language select are in a group hidden below lg; the menu holds all of it and
is itself hidden from lg up. No width is tried here (a template has none):
the classes that do the folding are what is read, and a browser at three
widths is the owner's script (dracena's ``tests/user_tests23.sh browser``).

    manage.py test toto.core.tests_app_bar_fold
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from toto.core.models import Platform
from toto.core.plugin import HeaderPlugin
from toto.core.tests_app_bar import _Tree


class _Pinned(HeaderPlugin):
    order = 1
    pinned = True

    def render_html(self, **kwargs):
        return f'<span data-widget="pinned" data-as="{kwargs.get("variant", "bar")}"></span>'


class _Folding(HeaderPlugin):
    order = 2

    def render_html(self, **kwargs):
        return f'<span data-widget="folding" data-as="{kwargs.get("variant", "bar")}"></span>'


def _inside(node, ident) -> bool:
    """Is ``node`` inside the element whose id is ``ident``?"""
    while node is not None:
        if node.attrs.get("id") == ident:
            return True
        node = node.parent
    return False


class FoldTests(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self._saved = dict(HeaderPlugin.registry)
        HeaderPlugin.registry.clear()
        HeaderPlugin.register(_Pinned)
        HeaderPlugin.register(_Folding)
        self.user = get_user_model().objects.create_user("ada", password="x")

    def tearDown(self):
        HeaderPlugin.registry.clear()
        HeaderPlugin.registry.update(self._saved)

    def bar(self, user=None, items=()):
        request = RequestFactory().get("/somewhere/")
        request.user = user or self.user
        html = render_to_string("oya/header.html", {"header_nav_items": list(items)}, request=request)
        return html, _Tree(html)

    def one(self, tree, ident):
        found = [n for n in tree.root.all() if n.attrs.get("id") == ident]
        self.assertEqual(len(found), 1, ident)
        return found[0]

    def widgets(self, tree, which, variant):
        return [n for n in tree.root.all()
                if n.attrs.get("data-widget") == which and n.attrs.get("data-as") == variant]

    def test_the_fold_is_at_lg_and_nowhere_else(self):
        html, tree = self.bar()
        nav = self.one(tree, "header-nav")
        folding = self.one(tree, "header-folding")
        box = self.one(tree, "header-menu-box")
        for hidden_when_folded in (nav, folding):
            self.assertIn("hidden", hidden_when_folded.classes())
            self.assertIn("lg:flex", hidden_when_folded.classes())
        # The button and its dropdown are one box, shown only while folded.
        self.assertIn("lg:hidden", box.classes())
        self.assertNotIn("hidden", box.classes())
        for inside in ("header-menu-toggle", "header-menu"):
            self.assertTrue(_inside(self.one(tree, inside), "header-menu-box"))
        # The old breakpoint is gone from every display class of the header:
        # a group that still folded at sm would open a second bar at 640 px.
        for node in tree.root.all():
            for name in node.classes():
                if name.startswith(("sm:", "md:")):
                    self.assertNotIn(name.split(":", 1)[1], ("hidden", "flex", "block", "inline-flex"),
                                     f"{node.tag} {node.attrs.get('id')}: {name}")
        self.assertNotIn("sm:hidden", html)

    def test_what_stays_is_the_brand_the_pinned_widgets_and_the_button(self):
        _, tree = self.bar()
        brand = self.one(tree, "brand-link")
        pinned = self.one(tree, "header-pinned")
        button = self.one(tree, "header-menu-toggle")
        row = brand.parent
        self.assertEqual(row.tag, "div")
        # One row: the button's box FIRST, so on the left, then the brand,
        # the pinned widgets, the folding ones, the links.
        self.assertEqual([child.attrs.get("id") for child in row.children],
                         ["header-menu-box", "brand-link", "header-pinned", "header-folding",
                          "header-nav"])
        # Nothing that stays is inside a group that hides.
        for stays in (brand, pinned, button):
            node = stays
            while node is not None:
                self.assertNotIn("hidden", node.classes(), stays.attrs.get("id"))
                node = node.parent
        # The pinned widget is drawn once on the bar, in the group that stays.
        on_bar = self.widgets(tree, "pinned", "bar")
        self.assertEqual(len(on_bar), 1)
        self.assertTrue(_inside(on_bar[0], "header-pinned"))
        # The name gives way before anything is pushed off the bar.
        self.assertIn("min-w-0", brand.classes())
        self.assertIn("mr-auto", brand.classes())
        heading = [n for n in brand.all() if n.tag == "h1"][0]
        self.assertIn("truncate", heading.classes())
        for keeps_its_width in (pinned, self.one(tree, "header-menu-box")):
            self.assertIn("shrink-0", keeps_its_width.classes())

    def test_every_other_widget_folds_without_saying_so_itself(self):
        _, tree = self.bar()
        on_bar = self.widgets(tree, "folding", "bar")
        self.assertEqual(len(on_bar), 1)
        self.assertTrue(_inside(on_bar[0], "header-folding"))
        self.assertFalse(_inside(on_bar[0], "header-pinned"))
        # The widget's own markup names no breakpoint: the header's group does.
        self.assertEqual(on_bar[0].classes(), [])

    def test_the_menu_holds_everything_that_folded(self):
        items = [{"url": "/drive/", "label": "Drive", "icon": "fa-solid fa-hard-drive"},
                 {"url": "/dash/", "label": "Dashboard", "icon": ""}]
        _, tree = self.bar(items=items)
        menu = self.one(tree, "header-menu")
        nav = self.one(tree, "header-nav")
        # Each widget once in the menu, as "mobile": the folded one, and the
        # pinned one's fuller row too.
        for which in ("folding", "pinned"):
            in_menu = self.widgets(tree, which, "mobile")
            self.assertEqual(len(in_menu), 1, which)
            self.assertTrue(_inside(in_menu[0], "header-menu"))
        for group in (nav, menu):
            with self.subTest(group=group.attrs["id"]):
                links = [n.attrs.get("href") for n in group.all() if n.tag == "a"]
                for item in items:
                    self.assertIn(item["url"], links)
                self.assertEqual(len([n for n in group.all() if n.tag == "button"
                                      and "site-theme-toggle-button" in n.classes()]), 1)
                self.assertEqual(len([n for n in group.all() if n.tag == "select"
                                      and n.attrs.get("name") == "language"]), 1)
                self.assertEqual(len([n for n in group.all() if n.tag == "form"
                                      and n.attrs.get("action", "").endswith("logout/")]), 1)

    def test_the_menu_button_is_named_and_says_whether_the_menu_is_open(self):
        _, tree = self.bar()
        button = self.one(tree, "header-menu-toggle")
        menu = self.one(tree, "header-menu")
        self.assertEqual(button.attrs.get("type"), "button")
        self.assertEqual(button.attrs.get("aria-label"), "Menu")
        self.assertEqual(button.attrs.get("title"), "Menu")
        self.assertEqual(button.attrs.get("aria-controls"), "header-menu")
        self.assertEqual(button.attrs.get("aria-expanded"), "false")
        self.assertEqual(button.attrs.get(":aria-expanded"), "navOpen ? 'true' : 'false'")
        self.assertEqual(button.attrs.get("@click"), "navOpen = !navOpen")
        self.assertEqual(button.words(), "")
        icons = [n for n in button.all() if n.tag == "i"]
        self.assertEqual(len(icons), 1)
        self.assertIn("fa-bars", icons[0].classes())
        # Closed until asked for, also before Alpine has run; Escape and a
        # click outside the bar close it.
        self.assertEqual(menu.attrs.get("x-show"), "navOpen")
        self.assertEqual(menu.attrs.get("style"), "display: none")
        header = [n for n in tree.root.all() if n.tag == "header"][0]
        self.assertEqual(header.attrs.get("x-data"), "{ navOpen: false }")
        self.assertEqual(header.attrs.get("@keydown.escape.window"), "navOpen = false")
        # A click outside lands on the backdrop under the dropdown.
        backdrop = [n for n in self.one(tree, "header-menu-box").children
                    if "inset-0" in n.classes()]
        self.assertEqual(len(backdrop), 1)
        self.assertEqual(backdrop[0].attrs.get("@click"), "navOpen = false")
        # The dropdown hangs from the button's left edge.
        for name in ("absolute", "left-0", "top-full"):
            self.assertIn(name, menu.classes())

    def test_a_group_with_no_widget_is_not_drawn(self):
        HeaderPlugin.registry.clear()
        _, tree = self.bar()
        ids = {n.attrs.get("id") for n in tree.root.all()}
        self.assertNotIn("header-pinned", ids)
        self.assertNotIn("header-folding", ids)
        row = self.one(tree, "brand-link").parent
        self.assertEqual([child.attrs.get("id") for child in row.children],
                         ["header-menu-box", "brand-link", "header-nav"])

    def test_signed_out_the_bar_folds_the_same(self):
        _, tree = self.bar(user=AnonymousUser())
        self.assertIn("lg:hidden", self.one(tree, "header-menu-box").classes())
        for group in (self.one(tree, "header-nav"), self.one(tree, "header-menu")):
            self.assertEqual(len([n for n in group.all() if n.tag == "a"
                                  and n.attrs.get("href", "").endswith("login/")]), 1)
