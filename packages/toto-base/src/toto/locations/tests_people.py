"""The People tab: who appears, at what precision, and who may look.

Every test here names the bug it prevents, in the style of `events/tests.py`,
because this is exactly the kind of rule somebody "simplifies" back to
`objects.all()` while the page keeps rendering and nothing looks wrong.

The load-bearing one is `test_the_resolver_and_the_queryset_agree`. Two spellings
of one rule is the shape `vault/access.py:118-121` warns about: the listing
drifts more generous than the door, and the thing the door refuses gets
published by the page beside it.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.locations import nearby
from toto.locations.models import Address
from toto.locations.people_access import (may_see_location, place_label,
                                          point_for, shared_people)
from toto.people.models import LocationSharing, Person

User = get_user_model()

#: Warsaw, a point 3.6 km from it, and one 53.8 km from it. FAR has to sit
#: INSIDE `nearby.MAX_RADIUS_KM` or "a wider radius reaches further" is
#: untestable — the first draft put it in Łódź, 118 km away, past the cap.
CENTRE = (52.2297, 21.0122)
NEARBY = (52.2600, 21.0300)       # 3.6 km
FAR = (52.7000, 21.2000)          # 53.8 km


class PeopleTestCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            active=True,
            defaults={"site_name": "Test", "author": "t",
                      "publication_year": 2026})

        self.viewer_user = User.objects.create_user("viewer", password="pw")
        self.viewer = self._person(self.viewer_user, "Viewer", CENTRE)
        self.client.force_login(self.viewer_user)

    def _person(self, user, name, point, sharing=LocationSharing.OFF):
        address = None
        if point is not None:
            address = Address.objects.create(
                street="Somewhere", locality_name="Town",
                state_or_province_name="Region",
                latitude=point[0], longitude=point[1])
        return Person.objects.create(
            user=user, display_name=name, address=address,
            location_sharing=sharing)

    def _other(self, name, point, sharing=LocationSharing.OFF):
        user = User.objects.create_user(name.lower(), password="pw")
        return self._person(user, name, point, sharing)

    def _search(self, **params):
        params.setdefault("lat", CENTRE[0])
        params.setdefault("lon", CENTRE[1])
        return self.client.get(reverse("locations:people"), params)


class OptInTests(PeopleTestCase):

    def test_a_new_person_appears_to_nobody(self):
        """The default is the whole feature. If this ever flips, every address
        on the platform is published by the next deploy."""
        self._other("Ada", NEARBY)
        self.assertEqual(list(shared_people(self.viewer_user)), [self.viewer])

    def test_switching_on_makes_you_findable(self):
        ada = self._other("Ada", NEARBY, LocationSharing.EXACT)
        self.assertIn(ada, shared_people(self.viewer_user))

    def test_switching_off_removes_you_at_once(self):
        """No grace period, no cache — off means gone on the next page load."""
        ada = self._other("Ada", NEARBY, LocationSharing.EXACT)
        ada.location_sharing = LocationSharing.OFF
        ada.save(update_fields=["location_sharing"])
        self.assertNotIn(ada, shared_people(self.viewer_user))
        self.assertFalse(may_see_location(self.viewer_user, ada))

    def test_an_address_is_not_consent(self):
        """Having filled in an address is not the same as agreeing to be mapped
        — an address is given for delivery and administration."""
        ada = self._other("Ada", NEARBY)
        self.assertFalse(may_see_location(self.viewer_user, ada))

    def test_you_always_see_yourself(self):
        """Otherwise switching sharing off hides your own pin and reads as a
        bug rather than as the setting working."""
        self.assertTrue(may_see_location(self.viewer_user, self.viewer))
        self.assertIn(self.viewer, shared_people(self.viewer_user))

    def test_an_anonymous_visitor_sees_nobody(self):
        self._other("Ada", NEARBY, LocationSharing.EXACT)
        self.client.logout()
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(list(shared_people(AnonymousUser())), [])

    def test_the_resolver_and_the_queryset_agree(self):
        """The one that stops the two spellings drifting.

        Every person, every setting, both with and without an address: the door
        and the listing must return the same answer or one of them is wrong.
        """
        self._other("Shared", NEARBY, LocationSharing.EXACT)
        self._other("Coarse", NEARBY, LocationSharing.APPROXIMATE)
        self._other("Hidden", NEARBY, LocationSharing.OFF)
        self._other("NoAddress", None, LocationSharing.EXACT)

        listed = set(shared_people(self.viewer_user).values_list("pk", flat=True))
        for person in Person.objects.all():
            with self.subTest(person=person.display_name):
                self.assertEqual(
                    may_see_location(self.viewer_user, person),
                    person.pk in listed,
                    f"door and listing disagree about {person.display_name}")

    def test_somebody_with_no_address_never_appears(self):
        ada = self._other("Ada", None, LocationSharing.EXACT)
        self.assertFalse(may_see_location(self.viewer_user, ada))
        self.assertNotIn(ada, shared_people(self.viewer_user))


class PrecisionTests(PeopleTestCase):

    def test_an_exact_sharer_publishes_their_real_point(self):
        ada = self._other("Ada", NEARBY, LocationSharing.EXACT)
        self.assertEqual(point_for(ada), NEARBY)

    def test_an_approximate_sharer_is_coarsened(self):
        ada = self._other("Ada", NEARBY, LocationSharing.APPROXIMATE)
        self.assertEqual(point_for(ada), (round(NEARBY[0], 2), round(NEARBY[1], 2)))

    def test_a_hidden_person_publishes_nothing(self):
        ada = self._other("Ada", NEARBY, LocationSharing.OFF)
        self.assertIsNone(point_for(ada))

    def test_the_exact_point_never_reaches_the_page(self):
        """The claim coarsening actually makes. Asserted on the response body,
        because a coarse marker beside an exact number in a data attribute
        would defeat the whole thing."""
        precise = (52.26123456, 21.03987654)
        self._other("Ada", precise, LocationSharing.APPROXIMATE)
        body = self._search().content.decode()
        self.assertNotIn("52.26123456", body)
        self.assertNotIn("21.03987654", body)

    def test_a_coarse_sharer_gets_a_locality_not_a_street(self):
        """A street name printed beside a coarse pin undoes the coarsening."""
        ada = self._other("Ada", NEARBY, LocationSharing.APPROXIMATE)
        self.assertNotIn("Somewhere", place_label(ada))
        self.assertIn("Town", place_label(ada))

    def test_an_exact_sharer_gets_the_full_address(self):
        ada = self._other("Ada", NEARBY, LocationSharing.EXACT)
        self.assertIn("Somewhere", place_label(ada))

    def test_a_pre_migration_address_still_resolves(self):
        """A row geocoded before `0005_address_latlon` has a geometry and NULL
        floats. Reading the floats alone would drop every older address."""
        ada = self._other("Ada", NEARBY, LocationSharing.EXACT)
        Address.objects.filter(pk=ada.address_id).update(
            latitude=None, longitude=None)
        ada.refresh_from_db()
        point = point_for(ada)
        if getattr(ada.address, "geometry", None) is None:
            self.skipTest("no geometry column on this build")
        self.assertIsNotNone(point)


class RadiusTests(PeopleTestCase):

    def test_somebody_inside_the_circle_is_found(self):
        ada = self._other("Ada", NEARBY, LocationSharing.EXACT)
        found = nearby.people_within(
            self.viewer_user, latitude=CENTRE[0], longitude=CENTRE[1],
            radius_km=10)
        self.assertIn(ada, [person for person, _ in found])

    def test_somebody_outside_it_is_not(self):
        self._other("Far", FAR, LocationSharing.EXACT)
        found = nearby.people_within(
            self.viewer_user, latitude=CENTRE[0], longitude=CENTRE[1],
            radius_km=10)
        # Empty, not ["Viewer"]: a search for neighbours does not return the
        # searcher — see the exclusion in nearby.people_within.
        self.assertEqual([p.display_name for p, _ in found], [])

    def test_a_wider_radius_reaches_further(self):
        far = self._other("Far", FAR, LocationSharing.EXACT)
        found = nearby.people_within(
            self.viewer_user, latitude=CENTRE[0], longitude=CENTRE[1],
            radius_km=100)
        self.assertIn(far, [person for person, _ in found])

    def test_results_are_nearest_first(self):
        self._other("Far", FAR, LocationSharing.EXACT)
        self._other("Near", NEARBY, LocationSharing.EXACT)
        found = nearby.people_within(
            self.viewer_user, latitude=CENTRE[0], longitude=CENTRE[1],
            radius_km=100)
        distances = [round(d, 3) for _, d in found]
        self.assertEqual(distances, sorted(distances))

    def test_the_radius_is_capped(self):
        """An unbounded radius is a different feature — 'everyone who shares,
        ranked' — and nobody consented to that one."""
        self._other("Far", FAR, LocationSharing.EXACT)
        found = nearby.people_within(
            self.viewer_user, latitude=CENTRE[0], longitude=CENTRE[1],
            radius_km=999999)
        for _, distance in found:
            self.assertLessEqual(distance, nearby.MAX_RADIUS_KM)

    def test_a_hidden_person_is_never_returned(self):
        """The radius narrows the door's answer; it must not widen it."""
        self._other("Hidden", NEARBY, LocationSharing.OFF)
        found = nearby.people_within(
            self.viewer_user, latitude=CENTRE[0], longitude=CENTRE[1],
            radius_km=50)
        self.assertNotIn("Hidden", [p.display_name for p, _ in found])


class CommunityFilterTests(PeopleTestCase):

    def setUp(self):
        super().setUp()
        from toto.socialhub.models import Community

        self.teachers = Community.objects.create(name="Teachers")
        self.students = Community.objects.create(name="Students")

    def test_the_filter_narrows_to_one_community(self):
        teacher = self._other("Teacher", NEARBY, LocationSharing.EXACT)
        student = self._other("Student", NEARBY, LocationSharing.EXACT)
        teacher.communities.add(self.teachers)
        student.communities.add(self.students)

        found = nearby.people_within(
            self.viewer_user, latitude=CENTRE[0], longitude=CENTRE[1],
            radius_km=50, community=self.teachers)
        names = [p.display_name for p, _ in found]
        self.assertIn("Teacher", names)
        self.assertNotIn("Student", names)

    def test_the_filter_cannot_reveal_a_hidden_person(self):
        """Narrowing must never widen — a filter that joined its own queryset
        would be the way this rule gets bypassed."""
        hidden = self._other("Hidden", NEARBY, LocationSharing.OFF)
        hidden.communities.add(self.teachers)
        found = nearby.people_within(
            self.viewer_user, latitude=CENTRE[0], longitude=CENTRE[1],
            radius_km=50, community=self.teachers)
        self.assertNotIn("Hidden", [p.display_name for p, _ in found])


    def test_a_circle_is_no_filter_for_a_member(self):
        """Circles are hidden from members (2026-09-28): picked or typed, a
        circle would say who is in it — so it is not offered, and a typed one
        narrows nothing."""
        from toto.socialhub.models import Community

        board = Community.objects.create(name="Board", is_circle=True)
        insider = self._other("Insider", NEARBY, LocationSharing.EXACT)
        insider.communities.add(board)
        self._other("Outsider", NEARBY, LocationSharing.EXACT)

        response = self._search(community=board.pk)
        self.assertNotIn(board, response.context["communities"])
        self.assertIn(self.teachers, response.context["communities"])
        self.assertIsNone(response.context["selected_community"])
        names = {row["person"].display_name for row in response.context["results"]}
        self.assertIn("Outsider", names)


class PageTests(PeopleTestCase):

    def test_the_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("locations:people"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_the_page_renders_and_lists_a_sharer(self):
        self._other("Ada", NEARBY, LocationSharing.EXACT)
        response = self._search()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada")

    def test_it_does_not_list_somebody_who_did_not_opt_in(self):
        self._other("Hidden", NEARBY, LocationSharing.OFF)
        self.assertNotContains(self._search(), "Hidden")

    def test_a_viewer_with_no_centre_is_told_so(self):
        """'We do not know where you are' and 'nobody is near you' look the
        same otherwise, and only one of them is the user's fault."""
        self.viewer.address = None
        self.viewer.save(update_fields=["address"])
        response = self.client.get(reverse("locations:people"))
        self.assertIsNone(response.context["centre"])

    def test_a_dropped_pin_moves_the_search(self):
        far = self._other("Far", FAR, LocationSharing.EXACT)
        response = self._search(lat=FAR[0], lon=FAR[1], radius=10)
        self.assertIn(far.pk, [row["person"].pk for row in response.context["results"]])

    def test_a_nonsense_pin_falls_back_rather_than_erroring(self):
        response = self._search(lat="north", lon="somewhere")
        self.assertEqual(response.status_code, 200)

    def test_an_out_of_range_pin_is_refused(self):
        """Latitude 900 is not a place; it must not reach the distance maths."""
        response = self._search(lat=900, lon=900)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["centre"], CENTRE)


class MapScriptTests(PeopleTestCase):
    """The inline map script is JAVASCRIPT — the two ways templating broke it.

    Both bugs rendered a page that looked fine and drew nothing: the script
    died at its first syntax error, so a populated people list showed an
    empty map with no server-side trace at all.
    """

    def _script(self, body):
        start = body.index("const people")
        return body[start:body.index("</script>", start)]

    def test_no_html_entities_reach_the_script(self):
        # The popup text went through a variable filter, so autoescape turned
        # its apostrophes into entities inside <script> — "expected
        # expression" at the ampersand. It is literal template text now,
        # which autoescape never touches.
        self._other("Ada", NEARBY, LocationSharing.EXACT)
        script = self._script(self._search().content.decode())
        self.assertNotIn("&#x27;", script)
        self.assertNotIn("&amp;", script)
        self.assertIn('"Searching from here. Drag to move."', script)

    def test_the_numbers_survive_the_polish_locale(self):
        # Under pl, an unlocalized {{ float }} is "52,2297" — a decimal comma
        # that is a JS syntax error. Every number in the script carries
        # |unlocalize, so the locale changes the prose and never the code.
        self._other("Ada", NEARBY, LocationSharing.EXACT)
        script = self._script(self.client.get(
            reverse("locations:people"),
            {"lat": CENTRE[0], "lon": CENTRE[1]},
            HTTP_ACCEPT_LANGUAGE="pl").content.decode())
        self.assertIn("lat: 52.26", script)
        self.assertNotIn("lat: 52,26", script)

    def test_the_default_view_script_is_clean_too(self):
        # A viewer with no pin of their own: nothing centres the search, so
        # the no-centre branch of the script renders — the other popup text.
        nobody = User.objects.create_user("newcomer", password="pw")
        self.client.force_login(nobody)
        body = self.client.get(reverse("locations:people")).content.decode()
        script = self._script(body)
        self.assertIn('"Drag me, then Search."', script)
        self.assertNotIn("&#x27;", script)
