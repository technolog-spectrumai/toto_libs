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
