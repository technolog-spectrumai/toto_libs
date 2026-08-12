"""The REPO_ACCESS gate: who may use git at all.

The functional suites run as staff (tests/base.py); this module is the
permission matrix. override_settings works because permissions.access_level
reads the setting lazily.
"""

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from toto.repo import permissions
from toto.repo.integration import context_for_directory, context_for_file

from .base import RepoTestCase

User = get_user_model()


class GateTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.plain = User.objects.create_user("plain", "p@example.com", "pw")
        self.repo = self.make_repo()

    def test_non_staff_is_refused_on_every_door(self):
        self.client.force_login(self.plain)

        for name, args, method in [
            ("repo:status", [self.repo.pk], "get"),
            ("repo:commit", [self.repo.pk], "post"),
            ("repo:init", [self.sub.pk], "post"),
            ("repo:run_status", [1], "get"),
        ]:
            response = getattr(self.client, method)(reverse(name, args=args))
            self.assertEqual(response.status_code, 403, name)
            self.assertIn("REPO_ACCESS", response.json()["error"])

        response = self.client.get(reverse("repo:index"))
        self.assertEqual(response.status_code, 403)

    def test_staff_passes_by_default(self):
        self.client.force_login(self.user)  # staff via base.py
        response = self.client.get(reverse("repo:status", args=[self.repo.pk]))
        self.assertEqual(response.status_code, 200)

    @override_settings(REPO_ACCESS="superuser")
    def test_superuser_level_refuses_staff(self):
        self.assertFalse(permissions.can_use(self.user))
        root = User.objects.create_superuser("root", "r@example.com", "pw")
        self.assertTrue(permissions.can_use(root))

        self.client.force_login(self.user)
        response = self.client.get(reverse("repo:status", args=[self.repo.pk]))
        self.assertEqual(response.status_code, 403)

    @override_settings(REPO_ACCESS="authenticated")
    def test_authenticated_level_admits_everyone_signed_in(self):
        self.assertTrue(permissions.can_use(self.plain))
        self.client.force_login(self.plain)
        response = self.client.get(reverse("repo:status", args=[self.repo.pk]))
        self.assertEqual(response.status_code, 200)

    def test_context_helpers_hide_the_toolbar_below_the_gate(self):
        self.assertIsNone(context_for_directory(self.root, self.plain))
        self.assertIsNone(context_for_file(self.f_notes, self.plain))

        ctx = context_for_directory(self.root, self.user)
        self.assertEqual(ctx["repo_pk"], self.repo.pk)
        self.assertIsNotNone(context_for_file(self.f_notes, self.user))

    def test_dashboard_tile_is_staff_visible(self):
        from django.conf import settings

        tile = next(item for item in settings.DASHBOARD_ITEMS
                    if item["link"] == "repo:index")
        self.assertEqual(tile["visibility"], "staff")
