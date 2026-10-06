"""Who belongs to a community, and who decides for it (2026-10-06).

``permissions.is_community_member``: among its members, or its head, or a
senior member. ``permissions.may_moderate_community``: its head, or an
administrator (a superuser on the Superuser plan); staff alone is not
enough, and neither is being a senior member. No geography in either.

    manage.py test toto.socialhub.tests_community_access
"""

import io

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.test import TestCase

from toto.people.models import Person
from toto.socialhub.models import Community
from toto.socialhub.permissions import is_community_member, may_moderate_community

User = get_user_model()


class CommunityAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def person(name, **flags):
            user = User.objects.create_user(name, password="pw", **flags)
            return user, Person.objects.create(user=user, display_name=name.title())

        cls.member_user, cls.member = person("mia")
        cls.head_user, cls.head = person("hugo")
        cls.senior_user, cls.senior = person("sen")
        cls.stranger_user, cls.stranger = person("stan")
        cls.staff_user, cls.staff = person("stef", is_staff=True)
        cls.bare_root_user, cls.bare_root = person("bare", is_superuser=True, is_staff=True)
        cls.guild = Community.objects.create(name="Guild", head=cls.head)
        cls.other = Community.objects.create(name="Other", head=cls.stranger)
        cls.member.communities.add(cls.guild)
        cls.guild.senior_members.add(cls.senior)
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())   # bare → the Superuser plan
        cls.root_user = User.objects.get(pk=cls.bare_root_user.pk)
        # A superuser made after the plans were handed out is on none.
        cls.late_root_user = User.objects.create_superuser("late", password="pw")
        Person.objects.create(user=cls.late_root_user, display_name="Late")

    def test_a_member_belongs(self):
        self.assertTrue(is_community_member(self.member_user, self.guild))
        self.assertFalse(is_community_member(self.member_user, self.other))

    def test_the_head_belongs_without_being_among_the_members(self):
        self.assertFalse(self.head.communities.filter(pk=self.guild.pk).exists())
        self.assertTrue(is_community_member(self.head_user, self.guild))

    def test_a_senior_member_belongs_without_being_among_the_members(self):
        self.assertFalse(self.senior.communities.filter(pk=self.guild.pk).exists())
        self.assertTrue(is_community_member(self.senior_user, self.guild))

    def test_a_stranger_staff_and_a_superuser_do_not_belong(self):
        for user in (self.stranger_user, self.staff_user, self.root_user):
            with self.subTest(user=user.username):
                self.assertFalse(is_community_member(user, self.guild))

    def test_a_signed_out_visitor_belongs_nowhere_and_decides_nothing(self):
        for user in (AnonymousUser(), None):
            self.assertFalse(is_community_member(user, self.guild))
            self.assertFalse(may_moderate_community(user, self.guild))

    def test_a_user_with_no_person_belongs_nowhere(self):
        bare = User.objects.create_user("nobody", password="pw")
        self.assertFalse(is_community_member(bare, self.guild))
        self.assertFalse(may_moderate_community(bare, self.guild))

    def test_no_community_is_no(self):
        self.assertFalse(is_community_member(self.member_user, None))
        self.assertFalse(may_moderate_community(self.head_user, None))

    def test_the_head_decides_for_their_own_community_only(self):
        self.assertTrue(may_moderate_community(self.head_user, self.guild))
        self.assertFalse(may_moderate_community(self.head_user, self.other))

    def test_a_member_and_a_senior_member_do_not_decide(self):
        self.assertFalse(may_moderate_community(self.member_user, self.guild))
        self.assertFalse(may_moderate_community(self.senior_user, self.guild))

    def test_staff_alone_is_not_enough(self):
        self.assertTrue(self.staff_user.is_staff)
        self.assertFalse(may_moderate_community(self.staff_user, self.guild))

    def test_a_superuser_on_the_superuser_plan_decides_everywhere(self):
        self.assertTrue(may_moderate_community(self.root_user, self.guild))
        self.assertTrue(may_moderate_community(self.root_user, self.other))

    def test_a_superuser_without_the_plan_does_not(self):
        from toto.socialhub.contact_access import is_administrator

        if is_administrator(self.late_root_user):
            self.skipTest("this host sells no Superuser plan: the privilege alone decides")
        self.assertFalse(may_moderate_community(self.late_root_user, self.guild))

    def test_the_two_rules_import_no_geography(self):
        from pathlib import Path

        from toto.socialhub import permissions

        source = Path(permissions.__file__).read_text(encoding="utf-8")
        self.assertNotIn("geography", source.replace("No geography", ""))
        self.assertNotIn("locations", source)
