"""The app-bar plugin slot: a registry, a tag, and two render passes.

Mirrors the floating registry's contract and is tested the way the steven
chip is (`toto.steven.tests_chat.ChipRenderTests`): a bare request, the
registry's `render_all`, and the joined HTML. The header template itself is
exercised by the host's page tests (`zenobia.tests.test_brand`), which is where
"the brand link survives the slot" is asserted against a real page.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import RequestFactory, TestCase

from toto.core.plugin import HeaderPlugin, RenderedHeaderPlugin


class _Chip(HeaderPlugin):
    """A throwaway widget: visible to the signed-in, and it can tell the two
    variants apart."""

    order = 5

    def is_visible(self, **kwargs):
        request = kwargs.get("request")
        user = getattr(request, "user", None)
        return bool(user is not None and user.is_authenticated)

    def render_html(self, **kwargs):
        return f'<span id="chip" data-variant="{kwargs.get("variant", "bar")}"></span>'


class _Second(HeaderPlugin):
    order = 1

    def render_html(self, **kwargs):
        return '<span id="second"></span>'


class _Stays(HeaderPlugin):
    """A widget that stays on the bar when the bar folds (2026-10-12)."""

    order = 3
    pinned = True

    def render_html(self, **kwargs):
        return '<span id="stays"></span>'


class HeaderPluginTests(TestCase):
    def setUp(self):
        self._saved = dict(HeaderPlugin.registry)
        HeaderPlugin.registry.clear()
        HeaderPlugin.register(_Chip)
        HeaderPlugin.register(_Second)
        self.user = get_user_model().objects.create_user("ada", password="x")

    def tearDown(self):
        HeaderPlugin.registry.clear()
        HeaderPlugin.registry.update(self._saved)

    def request(self, user=None):
        request = RequestFactory().get("/")
        request.user = user or AnonymousUser()
        return request

    def test_it_owns_its_own_registry(self):
        from toto.core.plugin import FloatingPlugin

        self.assertIn("_Chip", HeaderPlugin.registry)
        self.assertNotIn("_Chip", FloatingPlugin.registry)

    def test_render_all_orders_and_filters(self):
        rendered = HeaderPlugin.render_all(request=self.request(self.user))
        self.assertEqual([r.key for r in rendered], ["_Second", "_Chip"])
        self.assertTrue(all(isinstance(r, RenderedHeaderPlugin) for r in rendered))

    def test_a_widget_can_hide_itself(self):
        keys = [r.key for r in HeaderPlugin.render_all(request=self.request())]
        self.assertEqual(keys, ["_Second"])

    def test_the_tag_renders_both_variants(self):
        template = Template(
            '{% load plugin_tags %}[{% render_header_plugins %}]'
            '[{% render_header_plugins variant="mobile" %}]')
        html = template.render(Context({"request": self.request(self.user)}))
        self.assertIn('data-variant="bar"', html)
        self.assertIn('data-variant="mobile"', html)
        self.assertEqual(html.count('id="second"'), 2)

    def test_the_tag_is_empty_when_nothing_registers(self):
        HeaderPlugin.registry.clear()
        html = Template('{% load plugin_tags %}{% render_header_plugins %}').render(
            Context({"request": self.request(self.user)}))
        self.assertEqual(html, "")


class PinnedGroupTests(TestCase):
    """``pinned`` splits the registry in two for the header (2026-10-12): the
    widgets that stay on the bar at every width, and the ones the header
    folds into its menu. A widget is unpinned unless it says otherwise."""

    def setUp(self):
        self._saved = dict(HeaderPlugin.registry)
        HeaderPlugin.registry.clear()
        for plugin in (_Chip, _Second, _Stays):
            HeaderPlugin.register(plugin)
        self.request = RequestFactory().get("/")
        self.request.user = get_user_model().objects.create_user("ada", password="x")

    def tearDown(self):
        HeaderPlugin.registry.clear()
        HeaderPlugin.registry.update(self._saved)

    def keys(self, **kwargs):
        return [r.key for r in HeaderPlugin.render_all(request=self.request, **kwargs)]

    def test_a_widget_folds_unless_it_says_it_stays(self):
        self.assertIs(HeaderPlugin.pinned, False)
        self.assertIs(_Chip.pinned, False)
        self.assertIs(_Stays.pinned, True)

    def test_render_all_narrows_to_one_side(self):
        self.assertEqual(self.keys(), ["_Second", "_Stays", "_Chip"])
        self.assertEqual(self.keys(pinned=None), ["_Second", "_Stays", "_Chip"])
        self.assertEqual(self.keys(pinned=True), ["_Stays"])
        self.assertEqual(self.keys(pinned=False), ["_Second", "_Chip"])
        # ``pinned`` is the registry's own argument: no widget is handed it.
        seen = {}

        class _Asks(HeaderPlugin):
            order = 9

            def render_html(self, **kwargs):
                seen.update(kwargs)
                return ""

        HeaderPlugin.register(_Asks)
        HeaderPlugin.render_all(request=self.request, variant="mobile", pinned=False)
        self.assertEqual(sorted(seen), ["request", "variant"])

    def test_the_tag_draws_a_group(self):
        def drawn(arguments):
            return Template("{% load plugin_tags %}{% render_header_plugins " + arguments + " %}").render(
                Context({"request": self.request}))

        self.assertEqual(drawn('group="pinned"'), '<span id="stays"></span>')
        self.assertEqual(drawn('group="folding"'),
                         '<span id="second"></span><span id="chip" data-variant="bar"></span>')
        self.assertEqual(drawn('variant="mobile" group="folding"'),
                         '<span id="second"></span><span id="chip" data-variant="mobile"></span>')
        # Every widget when no group is named, as before.
        self.assertEqual(drawn('variant="mobile"').count("<span"), 3)
        # A word that is no group draws nothing, rather than everything twice.
        self.assertEqual(drawn('group="pined"'), "")

    def test_the_tag_can_be_kept_in_a_variable_and_tested_for_emptiness(self):
        template = Template(
            "{% load plugin_tags %}{% render_header_plugins group=\"pinned\" as stays %}"
            "{% if stays %}[{{ stays }}]{% else %}none{% endif %}")
        self.assertEqual(template.render(Context({"request": self.request})),
                         '[<span id="stays"></span>]')
        HeaderPlugin.registry.clear()
        self.assertEqual(template.render(Context({"request": self.request})), "none")

