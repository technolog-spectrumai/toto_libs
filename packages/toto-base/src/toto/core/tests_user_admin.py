"""No account is deleted in Django's admin (2026-10-01, 37c.21;
``toto.core.admin.ConsoleErasedUserAdmin``).

A delete there went by the bare cascade and skipped ``erase_user``: the
avatar's file, the version bodies, the forum's pictures and recordings, the
application and the home pin stayed, and no erasure request was closed or
recorded. The console is the one way to an erase, and the admin says so.

    manage.py test toto.core.tests_user_admin
"""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.admin import ConsoleErasedUserAdmin

User = get_user_model()


@override_settings(SOCIALHUB_ERASURE_COMMAND="python3 tools/delete_user.py {username}")
class UserAdminTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser("root", "root@example.com", "pw")
        self.ada = User.objects.create_user("ada", "ada@example.com", "pw")
        self.client.force_login(self.root)

    def test_the_user_admin_is_the_one_without_a_delete(self):
        self.assertIsInstance(admin.site._registry[User], ConsoleErasedUserAdmin)

    def test_the_list_offers_no_delete_and_names_the_console(self):
        response = self.client.get(reverse("admin:auth_user_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'value="delete_selected"')
        self.assertContains(response, "python3 tools/delete_user.py USERNAME")

    def test_the_bulk_delete_action_deletes_nobody(self):
        self.client.post(reverse("admin:auth_user_changelist"), {
            "action": "delete_selected", "_selected_action": [self.ada.pk], "post": "yes"})
        self.assertTrue(User.objects.filter(pk=self.ada.pk).exists())

    def test_the_delete_page_is_refused(self):
        url = reverse("admin:auth_user_delete", args=[self.ada.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.ada.pk).exists())

    def test_the_change_page_has_no_delete_link_and_names_the_command(self):
        response = self.client.get(reverse("admin:auth_user_change", args=[self.ada.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("admin:auth_user_delete", args=[self.ada.pk]))
        self.assertContains(response, "python3 tools/delete_user.py ada")

    def test_the_add_page_is_django_s_own(self):
        response = self.client.get(reverse("admin:auth_user_add"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "erase-at-console")
