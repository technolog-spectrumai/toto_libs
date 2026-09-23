"""A dashboard tile's visibility word decides who sees it — and a word the
resolver does not know must hide the tile, not show it to everyone.

Until 2026-09-23 an unknown word fell through every arm of
`_resolve_dashboard_item`: zenobia's Presentations tile said "authenticated"
and was public but for the login gate.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from toto.core.views import DASHBOARD_VISIBILITIES, _resolve_dashboard_item


def _tile(visibility):
    return {"title": "Probe", "description": "", "icon": "fa-solid fa-circle",
            "link": None, "visibility": visibility}


class UnknownVisibilityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.member = User.objects.create_user("member", password="pw")
        self.root = User.objects.create_superuser("root", password="pw")

    def test_an_unknown_word_hides_the_tile_from_everyone(self):
        for word in ("authenticated", "staf", "", None, 7):
            for user in (AnonymousUser(), self.member, self.root):
                with self.subTest(word=word, user=str(user)):
                    with self.assertLogs("toto.core.views", "WARNING"):
                        self.assertIsNone(_resolve_dashboard_item(_tile(word), user))

    def test_every_known_word_still_resolves_for_a_superuser(self):
        for word in DASHBOARD_VISIBILITIES + ("group:boards",):
            with self.subTest(word=word):
                self.assertIsNotNone(_resolve_dashboard_item(_tile(word), self.root))

    def test_private_is_what_signed_in_means(self):
        self.assertIsNone(_resolve_dashboard_item(_tile("private"), AnonymousUser()))
        self.assertIsNotNone(_resolve_dashboard_item(_tile("private"), self.member))
