"""No account is deleted in Django's admin (2026-10-01, 37c.21;
``toto.core.admin.ConsoleErasedUserAdmin``), and none is made there (37c.32).

A delete there went by the bare cascade and skipped ``erase_user``: the
avatar's file, the version bodies, the forum's pictures and recordings, the
application and the home pin stayed, and no erasure request was closed or
recorded. The console is the one way to an erase, and the admin says so.

The add page made an account — a superuser too — from a password typed into
a web form, for anybody holding an administrator's session. Accounts are made
at the console only (the owner's rule): no add page, no button, and the list
names the command (``ACCOUNT_CREATE_COMMAND``).

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


@override_settings(ACCOUNT_CREATE_COMMAND="python3 tools/create_user.py {username}",
                   SOCIALHUB_ERASURE_COMMAND="python3 tools/delete_user.py {username}")
class NoAccountIsMadeHereTests(TestCase):
    """37c.32: the add page, its button and the index's Add link are gone."""

    def setUp(self):
        self.root = User.objects.create_superuser("root", "root@example.com", "pw")
        self.client.force_login(self.root)

    def test_the_add_page_is_refused_and_makes_nobody(self):
        url = reverse("admin:auth_user_add")
        self.assertEqual(self.client.get(url).status_code, 403)
        response = self.client.post(url, {"username": "mallory", "password1": "Zq9!long-enough",
                                          "password2": "Zq9!long-enough"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username="mallory").exists())

    def test_the_list_has_no_add_button_and_names_the_command(self):
        response = self.client.get(reverse("admin:auth_user_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("admin:auth_user_add"))
        self.assertContains(response, 'data-testid="create-at-console"')
        self.assertContains(response, "python3 tools/create_user.py USERNAME")

    def test_the_index_offers_no_add_link_for_users(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("admin:auth_user_changelist"))
        self.assertNotContains(response, reverse("admin:auth_user_add"))

    def test_staff_with_every_user_right_cannot_add_either(self):
        from django.contrib.auth.models import Permission

        clerk = User.objects.create_user("clerk", "clerk@example.com", "pw", is_staff=True)
        clerk.user_permissions.set(Permission.objects.filter(content_type__app_label="auth",
                                                             content_type__model="user"))
        self.client.force_login(clerk)
        self.assertEqual(self.client.get(reverse("admin:auth_user_add")).status_code, 403)


class CreateCommandTests(TestCase):
    def test_a_host_without_a_wrapper_hears_of_bootstrap_users(self):
        from toto.core.admin import DEFAULT_CREATE_COMMAND, create_command

        with override_settings(ACCOUNT_CREATE_COMMAND=""):
            self.assertEqual(create_command(), DEFAULT_CREATE_COMMAND)

    def test_the_host_s_wrapper_gets_the_name_shell_quoted(self):
        from toto.core.admin import create_command

        with override_settings(ACCOUNT_CREATE_COMMAND="python3 tools/create_user.py {username}"):
            self.assertEqual(create_command("o'brien"),
                             "python3 tools/create_user.py 'o'\"'\"'brien'")
