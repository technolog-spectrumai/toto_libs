"""Tests for the shared Knowledge Graph tab bar and the bento 'Data' tab.

Bento is surfaced inside ravioli as the 'Data' tab at /ravioli/data/. The
``ravioli/_tabs.html`` partial is rendered on every ravioli page *and* every
bento page, so there is always a one-click way to move between them.
"""

from django.contrib.auth.models import AnonymousUser, User
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse


class DataTabUrlTests(TestCase):
    def test_data_tab_mounted_under_ravioli(self):
        self.assertEqual(reverse("bento:node_list"), "/ravioli/data/")


class TabBarPartialTests(TestCase):
    """The partial renders the four tabs and gates Graph Analysis to superusers."""

    def _render(self, active_tab="queries", user=None):
        ctx = {"active_tab": active_tab}
        if user is not None:
            req = RequestFactory().get("/")
            req.user = user
            ctx["request"] = req
        return render_to_string("ravioli/_tabs.html", ctx)

    def test_always_present_tabs_render(self):
        html = self._render()
        self.assertIn(reverse("ravioli:query_unified"), html)  # Knowledge Graph
        self.assertIn(reverse("ravioli:search"), html)         # Search
        self.assertIn(reverse("bento:node_list"), html)        # Data

    def test_graph_analysis_tab_hidden_for_anonymous(self):
        html = self._render(user=AnonymousUser())
        self.assertNotIn(reverse("ravioli:graph_analysis"), html)

    def test_graph_analysis_tab_shown_for_superuser(self):
        su = User.objects.create_superuser("su", "su@example.com", "pw")
        html = self._render(user=su)
        self.assertIn(reverse("ravioli:graph_analysis"), html)

    def test_active_tab_marked_current(self):
        self.assertIn('aria-current="page"', self._render(active_tab="data"))
