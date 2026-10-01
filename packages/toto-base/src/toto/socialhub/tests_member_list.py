"""The member roster's order (37c.22, 2026-10-01).

Person has no default order and the roster paginates, so each page was a
slice of whatever order the database chose that time: a member could show on
two pages or on none, and Django warned on every request
(UnorderedObjectListWarning). By display name, then id.
"""

import warnings

from django.contrib.auth import get_user_model
from django.core.paginator import UnorderedObjectListWarning
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person

User = get_user_model()


class MemberListOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            active=True, defaults={"site_name": "Test", "author": "t", "publication_year": 2026})
        cls.viewer = User.objects.create_user("viewer", password="pw")
        # Created against the alphabet, and with a name twice, so neither the
        # order they were made in nor the name alone gives the right answer.
        for name in ("Zofia", "Ula", "Tomasz", "Bartek", "Stefan", "Roman", "Piotr",
                     "Bartek", "Olga", "Natalia", "Marek", "Adam"):
            Person.objects.create(display_name=name)

    def setUp(self):
        self.client.force_login(self.viewer)

    def expected(self):
        return sorted(Person.objects.values_list("display_name", "id"))

    def listed(self, page=1):
        response = self.client.get(reverse("socialhub:profile_list"), {"page": page})
        self.assertEqual(response.status_code, 200)
        return [(p.display_name, p.pk) for p in response.context["profiles"]]

    def test_the_roster_is_paginated_without_the_unordered_warning(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnorderedObjectListWarning)
            self.listed()

    def test_every_member_is_on_exactly_one_page_by_name_then_id(self):
        first, second = self.listed(1), self.listed(2)
        self.assertEqual(len(first), 10)
        self.assertEqual(first + second, self.expected())

    def test_the_same_name_is_ordered_by_id(self):
        barteks = [pk for name, pk in self.listed(1) if name == "Bartek"]
        self.assertEqual(len(barteks), 2)
        self.assertEqual(barteks, sorted(barteks))
