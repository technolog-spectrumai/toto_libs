"""The Person record (2026-09-29): its address in URLs, the name it goes by,
the privacy default it starts from, and what happens to it when the account
or a patron goes.

`toto.people` had no tests of its own; the Person is reached everywhere else
(socialhub's profiles, the API's /me/, the erase report) by these rules."""

import unittest

from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.people.models import LocationSharing, Person

User = get_user_model()


class SlugTests(TestCase):
    def test_the_slug_comes_from_the_display_name(self):
        self.assertEqual(Person.objects.create(display_name="Ada Lovelace").slug, "ada-lovelace")

    def test_a_second_person_of_the_same_name_gets_the_next_number(self):
        Person.objects.create(display_name="Jan Kowalski")
        second = Person.objects.create(display_name="Jan Kowalski")
        third = Person.objects.create(display_name="jan  kowalski")
        self.assertEqual((second.slug, third.slug), ("jan-kowalski-1", "jan-kowalski-2"))

    def test_a_slug_once_given_survives_a_rename(self):
        person = Person.objects.create(display_name="Old Name")
        person.display_name = "New Name"
        person.save()
        self.assertEqual(Person.objects.get(pk=person.pk).slug, "old-name")

    def test_a_chosen_slug_is_kept(self):
        self.assertEqual(Person.objects.create(display_name="Ada", slug="the-countess").slug,
                         "the-countess")

    def test_accented_letters_are_folded_to_ascii(self):
        self.assertEqual(Person.objects.create(display_name="Zażółć Gęślą").slug, "zazoc-gesla")

    @unittest.skip("SUSPECTED BUG toto/people/models.py:130-139 - a display name with no "
                   "ASCII letters (\"Олена\", \"李雷\") slugifies to '' and the Person is "
                   "saved with slug '': reverse('socialhub:profile_details') raises "
                   "NoReverseMatch and /api/me/ hands out /socialhub/profiles//. "
                   "verbena.utils.unique_slug has a fallback for exactly this.")
    def test_a_name_with_no_latin_letters_still_gets_an_address(self):
        from django.urls import reverse

        person = Person.objects.create(display_name="Олена")
        self.assertTrue(person.slug)
        reverse("socialhub:profile_details", args=[person.slug])


class NameTests(TestCase):
    def test_the_display_name_is_the_name(self):
        person = Person(display_name="Ada")
        self.assertEqual((str(person), person.full_name), ("Ada", "Ada"))

    def test_without_a_display_name_the_account_s_full_name_then_username(self):
        user = User.objects.create_user("ada", password="x", first_name="Ada", last_name="L.")
        self.assertEqual(Person(display_name="", user=user).full_name, "Ada L.")
        plain = User.objects.create_user("bob", password="x")
        self.assertEqual(Person(display_name="", user=plain).full_name, "bob")

    def test_nobody_at_all_is_an_unnamed_member(self):
        self.assertEqual(Person(display_name="").full_name, "Unnamed Member")


class DefaultsTests(TestCase):
    def test_nobody_s_home_is_shown_until_they_say_so(self):
        person = Person.objects.create(display_name="Ada")
        self.assertEqual(person.location_sharing, LocationSharing.OFF)
        self.assertEqual(Person.LOCATION_SHARING_CHOICES[0][0], "off")

    def test_the_language_starts_as_english_and_no_federation_subject(self):
        person = Person.objects.create(display_name="Ada")
        self.assertEqual((person.preferred_language, person.federated_sub), ("en", ""))


class LifecycleTests(TestCase):
    def test_the_person_goes_with_the_account(self):
        user = User.objects.create_user("ada", password="x")
        Person.objects.create(display_name="Ada", user=user)
        user.delete()
        self.assertFalse(Person.objects.filter(display_name="Ada").exists())

    def test_a_person_without_an_account_is_allowed(self):
        self.assertIsNone(Person.objects.create(display_name="Guest").user_id)

    def test_mentees_stay_when_their_patron_goes(self):
        patron = Person.objects.create(display_name="Patron")
        mentee = Person.objects.create(display_name="Mentee", patron=patron)
        self.assertEqual(list(patron.mentees.all()), [mentee])
        patron.delete()
        mentee.refresh_from_db()
        self.assertIsNone(mentee.patron_id)

    def test_the_account_reaches_its_person(self):
        user = User.objects.create_user("ada", password="x")
        person = Person.objects.create(display_name="Ada", user=user)
        self.assertEqual(User.objects.get(pk=user.pk).community_profile, person)
