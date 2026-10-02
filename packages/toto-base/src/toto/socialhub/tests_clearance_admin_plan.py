"""Clearances in Django's admin follow the Clearances tab's rule (2026-10-02,
the crown bug hunt).

The tab asks for a real superuser on the Superuser plan
(``views.clearances.may_manage``, the review of stage 37c). The admin did
not: ``ClearanceAdmin`` let in any superuser — one off the plan made
clearances, gave them to members and deleted them there — and the person
admin showed ``Person.clearances`` as an ordinary field to anybody holding
``people.change_person``, so a staff clerk with that one right ticked any
clearance on their own person and read every kept bucket, topic and map
domain from then on. Now both ask the tab's rule: the clearance pages are
shut to everybody else, and on a person the field is read-only for them.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_clearance_admin_plan
"""

import io

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.socialhub.views.clearances import may_manage

User = get_user_model()


def client_for(user):
    client = Client()
    client.force_login(user)
    return client


def person_form(person, **extra):
    """The person admin's change form as a browser posts it."""
    return {"user": person.user_id, "display_name": person.display_name, "slug": person.slug,
            "joined_date_0": "2026-09-28", "joined_date_1": "10:00:00",
            "location_sharing": "off", "preferred_language": "en", **extra}


class AdminPlanCase(TestCase):
    """``root`` is on the Superuser plan; ``bare_root``, made after the plans,
    is a superuser off it; ``clerk`` is staff with ``people.change_person``
    and nothing else."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.root = User.objects.create_superuser("root", "root@example.com", "pw")
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())
        cls.bare_root = User.objects.create_superuser("bareroot", "bare@example.com", "pw")
        cls.internal = Clearance.objects.create(name="internal", slug="internal")
        cls.secret = Clearance.objects.create(name="secret", slug="secret")
        cls.clerk = User.objects.create_user("clerk", "clerk@example.com", "pw", is_staff=True)
        cls.clerk.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="people", codename__in=["change_person", "view_person"]))
        cls.clerk_person = Person.objects.create(user=cls.clerk, display_name="Clerk", slug="clerk")
        cls.ada = Person.objects.create(
            user=User.objects.create_user("ada", "ada@example.com", "pw"),
            display_name="Ada", slug="ada")

    def setUp(self):
        # The rule under test, as the tab asks it.
        self.assertTrue(may_manage(User.objects.get(pk=self.root.pk)))
        if apps.is_installed("toto.subscriptions"):
            self.assertFalse(may_manage(self.bare_root))


class ClearanceAdminTests(AdminPlanCase):
    def test_a_superuser_off_the_plan_gets_no_clearance_page(self):
        client = client_for(self.bare_root)
        for name, args in (("changelist", []), ("add", []), ("change", [self.internal.pk]),
                           ("delete", [self.internal.pk])):
            with self.subTest(page=name):
                self.assertEqual(client.get(reverse(f"admin:socialhub_clearance_{name}",
                                                    args=args)).status_code, 403)
        self.assertNotContains(client.get(reverse("admin:index")), "/admin/socialhub/clearance/")

    def test_nor_makes_gives_or_deletes_one_there(self):
        client = client_for(self.bare_root)
        made = client.post(reverse("admin:socialhub_clearance_add"), {
            "name": "payroll", "slug": "payroll", "regen_security": "", "regen_compute": "",
            "regen_storage": "", "members": [self.ada.pk]})
        given = client.post(reverse("admin:socialhub_clearance_change", args=[self.internal.pk]), {
            "name": "internal", "slug": "internal", "regen_security": "9", "regen_compute": "",
            "regen_storage": "", "members": [self.ada.pk]})
        deleted = client.post(reverse("admin:socialhub_clearance_delete", args=[self.secret.pk]),
                              {"post": "yes"})
        self.assertEqual([made.status_code, given.status_code, deleted.status_code],
                         [403, 403, 403])
        self.assertEqual(set(Clearance.objects.values_list("slug", flat=True)),
                         {"internal", "secret"})
        self.assertFalse(self.ada.clearances.exists())
        self.internal.refresh_from_db()
        self.assertIsNone(self.internal.regen_security)

    def test_a_superuser_on_the_plan_still_manages_them(self):
        client = client_for(self.root)
        self.assertEqual(client.get(reverse("admin:socialhub_clearance_changelist")).status_code,
                         200)
        response = client.post(reverse("admin:socialhub_clearance_change",
                                       args=[self.internal.pk]), {
            "name": "internal", "slug": "internal", "regen_security": "", "regen_compute": "",
            "regen_storage": "", "members": [self.ada.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(self.ada.clearances.all()), {self.internal})


class PersonAdminClearancesTests(AdminPlanCase):
    def form_fields(self, user, person):
        response = client_for(user).get(reverse("admin:people_person_change", args=[person.pk]))
        self.assertEqual(response.status_code, 200)
        return response, response.context["adminform"].form.fields

    def test_a_clerk_cannot_give_themselves_a_clearance(self):
        response, fields = self.form_fields(self.clerk, self.clerk_person)
        self.assertNotIn("clearances", fields)
        self.assertNotContains(response, 'name="clearances"')
        posted = client_for(self.clerk).post(
            reverse("admin:people_person_change", args=[self.clerk_person.pk]),
            person_form(self.clerk_person, clearances=[self.internal.pk, self.secret.pk]))
        self.assertEqual(posted.status_code, 302)                 # the rest is saved
        self.assertFalse(self.clerk_person.clearances.exists())

    def test_a_clerk_sees_the_clearances_a_person_holds_and_changes_nothing_else(self):
        self.ada.clearances.add(self.internal)
        response, fields = self.form_fields(self.clerk, self.ada)
        self.assertNotIn("clearances", fields)
        self.assertContains(response, "internal")
        client_for(self.clerk).post(reverse("admin:people_person_change", args=[self.ada.pk]),
                                    person_form(self.ada, display_name="Ada L."))
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.display_name, "Ada L.")
        self.assertEqual(set(self.ada.clearances.all()), {self.internal})

    def test_a_superuser_off_the_plan_neither(self):
        _, fields = self.form_fields(self.bare_root, self.ada)
        self.assertNotIn("clearances", fields)
        client_for(self.bare_root).post(
            reverse("admin:people_person_change", args=[self.ada.pk]),
            person_form(self.ada, clearances=[self.secret.pk]))
        self.assertFalse(self.ada.clearances.exists())

    def test_nor_on_a_person_they_add(self):
        response = client_for(self.bare_root).get(reverse("admin:people_person_add"))
        self.assertNotIn("clearances", response.context["adminform"].form.fields)

    def test_a_clerk_cannot_move_a_cleared_person_onto_their_own_account(self):
        # The clearances are read-only, but the account the person signs in
        # with was not: unlinking their own person, then linking Ada's to
        # their account, made Ada's clearances the clerk's (41.5, the review).
        self.ada.clearances.add(self.secret)
        client = client_for(self.clerk)
        client.post(reverse("admin:people_person_change", args=[self.clerk_person.pk]),
                    person_form(self.clerk_person, user=""))
        _, fields = self.form_fields(self.clerk, self.ada)
        self.assertNotIn("user", fields)
        client.post(reverse("admin:people_person_change", args=[self.ada.pk]),
                    person_form(self.ada, user=self.clerk.pk))
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.user.username, "ada")
        self.assertFalse(Person.objects.filter(user=self.clerk, clearances=self.secret).exists())

    def test_a_clerk_still_links_a_person_who_holds_no_clearance(self):
        spare = User.objects.create_user("spare", "spare@example.com", "pw")
        _, fields = self.form_fields(self.clerk, self.ada)
        self.assertIn("user", fields)
        client_for(self.clerk).post(reverse("admin:people_person_change", args=[self.ada.pk]),
                                    person_form(self.ada, user=spare.pk))
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.user_id, spare.pk)

    def test_a_superuser_on_the_plan_moves_a_cleared_person(self):
        self.ada.clearances.add(self.secret)
        spare = User.objects.create_user("spare", "spare@example.com", "pw")
        _, fields = self.form_fields(self.root, self.ada)
        self.assertIn("user", fields)
        client_for(self.root).post(reverse("admin:people_person_change", args=[self.ada.pk]),
                                   person_form(self.ada, user=spare.pk, clearances=[self.secret.pk]))
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.user_id, spare.pk)

    def test_a_superuser_on_the_plan_gives_one_on_the_person(self):
        _, fields = self.form_fields(self.root, self.ada)
        self.assertIn("clearances", fields)
        posted = client_for(self.root).post(
            reverse("admin:people_person_change", args=[self.ada.pk]),
            person_form(self.ada, clearances=[self.secret.pk]))
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(set(self.ada.clearances.all()), {self.secret})
