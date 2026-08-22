"""Room polls: engine objects, room UI, room isolation.

The forum bar applies: a non-member is refused, room B's content never
appears in room A, and access dies when the member leaves — here enforced
twice, by the view gate AND by the engine's room electorate.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.forum import voting
from toto.forum.models import ForumChannel, ForumMember

User = get_user_model()


class RoomPollBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform
        from toto.people.models import Person

        Platform.objects.get_or_create(
            site_name="Toto", defaults={"author": "Test",
                                        "publication_year": 2026})
        cls.member_user = User.objects.create_user(username="m", password="x")
        cls.member_person = Person.objects.create(user=cls.member_user,
                                                  display_name="M")
        cls.outsider = User.objects.create_user(username="o", password="x")
        Person.objects.create(user=cls.outsider, display_name="O")
        cls.room = ForumChannel.objects.create(name="Alpha", slug="alpha")
        cls.other_room = ForumChannel.objects.create(name="Beta", slug="beta")
        cls.membership = ForumMember.objects.create(
            channel=cls.room, person=cls.member_person, is_active=True)

    def _poll(self, room=None, title="Lunch?"):
        return voting.open_room_poll(room or self.room, self.member_user,
                                     title=title, options="Pizza\nSushi")


class RoomPollViewTests(RoomPollBase):
    def test_a_member_creates_a_poll_through_the_modal(self):
        self.client.force_login(self.member_user)

        response = self.client.post(
            reverse("forum:room_poll_create", args=[self.room.slug]),
            {"title": "Lunch?", "options": "Pizza: hot\nSushi"})

        self.assertEqual(response.status_code, 302)
        question = voting.questions_for(self.room).get()
        self.assertEqual(
            [(c.label, c.text) for c in question.choices.all()],
            [("Pizza", "hot"), ("Sushi", "")])
        self.assertIsNone(question.closes_at)

    def test_one_option_is_refused(self):
        self.client.force_login(self.member_user)

        self.client.post(
            reverse("forum:room_poll_create", args=[self.room.slug]),
            {"title": "Lunch?", "options": "Pizza"})

        self.assertFalse(voting.questions_for(self.room).exists())

    def test_a_non_member_is_refused_everywhere(self):
        self._poll()
        self.client.force_login(self.outsider)

        self.assertEqual(self.client.get(
            reverse("forum:room_polls", args=[self.room.slug])).status_code,
            403)
        self.assertEqual(self.client.post(
            reverse("forum:room_poll_create", args=[self.room.slug]),
            {"title": "X", "options": "A\nB"}).status_code, 403)

    def test_a_member_votes_and_revises(self):
        question = self._poll()
        pizza, sushi = question.choices.all()
        self.client.force_login(self.member_user)
        url = reverse("forum:room_poll_vote",
                      args=[self.room.slug, question.slug])

        self.client.post(url, {"choice": pizza.pk})
        self.client.post(url, {"choice": sushi.pk})

        ballot = question.ballots.get()
        self.assertEqual(ballot.choice_id, sushi.pk)  # polls are revisable

    def test_room_bs_poll_never_lists_in_room_a(self):
        ForumMember.objects.create(channel=self.other_room,
                                   person=self.member_person, is_active=True)
        self._poll(room=self.other_room, title="Beta secret")
        self.client.force_login(self.member_user)

        response = self.client.get(
            reverse("forum:room_polls", args=[self.room.slug]))

        self.assertNotContains(response, "Beta secret")

    def test_room_polls_never_reach_the_global_polls_app(self):
        self._poll(title="Room only")
        self.client.force_login(self.member_user)

        response = self.client.get(reverse("polls:poll_list"))

        self.assertNotContains(response, "Room only")


class RoomAudienceTests(RoomPollBase):
    """The second line of defence: the ENGINE refuses non-members.

    A room's polls are answered by the room — a visibility rule, the same one
    that decides who reads its messages. Refused here and not merely in the
    view, because a mis-scoped question that reaches somebody must get "no"
    rather than a response filed into the wrong room.
    """

    def test_the_engine_refuses_a_non_member_directly(self):
        from toto.polls import services as polls_services
        from toto.polls.core import NotEligible

        question = self._poll()

        with self.assertRaises(NotEligible):
            polls_services.cast(question, self.outsider,
                                question.choices.first())

    def test_leaving_the_room_revokes_voting(self):
        from toto.polls import services as polls_services

        question = self._poll()
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])

        verdict = polls_services.standing(question, self.member_user)

        self.assertFalse(verdict.allowed)

    def test_the_audience_counts_active_members_only(self):
        from toto.people.models import Person
        from toto.polls import services as polls_services

        ghost_user = User.objects.create_user(username="g", password="x")
        ghost = Person.objects.create(user=ghost_user, display_name="G")
        ForumMember.objects.create(channel=self.room, person=ghost,
                                   is_active=False)
        question = self._poll()

        self.assertEqual(polls_services.tally(question).electorate, 1)

    def test_a_poll_outside_a_room_is_open_to_everyone_signed_in(self):
        """The seam is per place, not per question: an ordinary poll keeps
        the lightweight default."""
        from toto.polls import services as polls_services
        from toto.polls.core import OpenToAll

        question = self._poll()
        question.scope_type = ""
        self.assertIsInstance(polls_services.electorate_for(question), OpenToAll)

    def test_a_room_that_is_gone_admits_nobody(self):
        from toto.polls import services as polls_services

        question = self._poll()
        self.room.delete()
        verdict = polls_services.standing(question, self.member_user)
        self.assertFalse(verdict.allowed)

    def test_a_question_from_another_room_is_refused_by_belongs(self):
        from toto.forum.audience import RoomAudience

        question = self._poll()

        verdict = RoomAudience(self.other_room).standing(question,
                                                         self.member_user)

        self.assertFalse(verdict.allowed)
        self.assertIn("another room", verdict.reason)
