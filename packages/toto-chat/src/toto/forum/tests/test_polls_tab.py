"""Room polls: the forum's own data, the forum's own rules, room isolation.

The forum bar applies: a non-member is refused, room B's content never appears
in room A, and access dies when the member leaves — here enforced twice, by the
view gate AND inside `voting.cast`, because the door could be reached another
way one day and the engine must not depend on who called it.

These used to test a poll living in `toto.polls` behind a `(scope_type,
scope_id)` string. The models are the forum's now and the room is a real
ForeignKey, so the isolation tests below assert something stronger than they
did: not "the query filters by scope" but "the row cannot belong anywhere else".
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from toto.forum import voting
from toto.forum.models import (ForumChannel, ForumMember, PollBallot,
                               ResultVisibility, Revisability, RoomPoll)

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
        cls.staff = User.objects.create_user(username="s", password="x",
                                             is_staff=True)
        cls.staff_person = Person.objects.create(user=cls.staff,
                                                 display_name="S")
        cls.room = ForumChannel.objects.create(name="Alpha", slug="alpha")
        cls.other_room = ForumChannel.objects.create(name="Beta", slug="beta")
        cls.membership = ForumMember.objects.create(
            channel=cls.room, person=cls.member_person, is_active=True)
        ForumMember.objects.create(channel=cls.room, person=cls.staff_person,
                                   is_active=True)

    def _poll(self, room=None, title="Lunch?", **kwargs):
        return voting.open_poll(room or self.room, self.member_user,
                                title=title, options="Pizza\nSushi", **kwargs)


class RoomPollViewTests(RoomPollBase):
    def test_a_member_creates_a_poll_through_the_modal(self):
        self.client.force_login(self.member_user)
        response = self.client.post(
            reverse("forum:room_poll_create", args=[self.room.slug]),
            {"title": "Lunch?", "options": "Pizza: the round one\nSushi"})
        self.assertEqual(response.status_code, 302)
        poll = RoomPoll.objects.get()
        self.assertEqual(poll.channel, self.room)
        self.assertEqual([c.label for c in poll.choices.all()],
                         ["Pizza", "Sushi"])
        self.assertEqual(poll.choices.first().text, "the round one")

    def test_one_option_is_refused(self):
        self.client.force_login(self.member_user)
        self.client.post(
            reverse("forum:room_poll_create", args=[self.room.slug]),
            {"title": "Lunch?", "options": "Pizza"})
        self.assertEqual(RoomPoll.objects.count(), 0)

    def test_a_member_votes_and_revises(self):
        poll = self._poll()
        pizza, sushi = poll.choices.all()
        self.client.force_login(self.member_user)
        url = reverse("forum:room_poll_vote", args=[self.room.slug, poll.slug])
        self.client.post(url, {"choice": pizza.pk})
        self.client.post(url, {"choice": sushi.pk})
        ballot = PollBallot.objects.get()
        self.assertEqual(ballot.choice, sushi)
        self.assertEqual(ballot.revisions, 1)
        self.assertIsNotNone(ballot.revised_at)

    def test_a_non_member_is_refused_everywhere(self):
        poll = self._poll()
        self.client.force_login(self.outsider)
        for url, payload in (
            (reverse("forum:room_polls", args=[self.room.slug]), None),
            (reverse("forum:room_poll_create", args=[self.room.slug]),
             {"title": "x", "options": "a\nb"}),
            (reverse("forum:room_poll_vote", args=[self.room.slug, poll.slug]),
             {"choice": poll.choices.first().pk}),
            (reverse("forum:room_poll_close", args=[self.room.slug, poll.slug]),
             {}),
            (reverse("forum:room_poll_delete", args=[self.room.slug, poll.slug]),
             {}),
        ):
            with self.subTest(url=url):
                response = (self.client.get(url) if payload is None
                            else self.client.post(url, payload))
                self.assertEqual(response.status_code, 403)

    def test_room_bs_poll_never_lists_in_room_a(self):
        self._poll(room=self.other_room, title="Beta business")
        self._poll(title="Alpha business")
        self.client.force_login(self.member_user)
        response = self.client.get(
            reverse("forum:room_polls", args=[self.room.slug]))
        self.assertContains(response, "Alpha business")
        self.assertNotContains(response, "Beta business")

    def test_the_polls_tab_needs_no_separate_engine(self):
        """The tab used to hide itself unless `toto.polls` was installed.
        The room owns its polls now, so the tab is simply always there."""
        self.client.force_login(self.member_user)
        response = self.client.get(
            reverse("forum:channel_detail", args=[self.room.slug]))
        self.assertContains(response,
                            reverse("forum:room_polls", args=[self.room.slug]))


class PollIsolationTests(RoomPollBase):
    def test_a_poll_belongs_to_one_room_by_foreign_key(self):
        poll = self._poll()
        self.assertEqual(poll.channel_id, self.room.pk)
        self.assertEqual(list(self.other_room.polls.all()), [])

    def test_deleting_a_room_takes_its_polls_and_answers(self):
        """What a room-owned poll means: the room is the owner, so removing
        the room removes the question and every answer to it."""
        poll = self._poll()
        voting.cast(poll, self.member_user, poll.choices.first())
        self.room.delete()
        self.assertEqual(RoomPoll.objects.count(), 0)
        self.assertEqual(PollBallot.objects.count(), 0)

    def test_two_rooms_may_both_hold_a_poll_of_the_same_name(self):
        """The slug is unique per room, not globally — the whole reason the
        constraint is scoped."""
        first = self._poll(title="Lunch?")
        second = self._poll(room=self.other_room, title="Lunch?")
        self.assertEqual(first.slug, second.slug)

    def test_a_choice_from_another_poll_is_refused(self):
        mine = self._poll(title="Mine")
        theirs = self._poll(room=self.other_room, title="Theirs")
        with self.assertRaises(voting.UnknownChoice):
            voting.cast(mine, self.member_user, theirs.choices.first())


class AnswerRuleTests(RoomPollBase):
    def test_the_engine_refuses_a_non_member_directly(self):
        poll = self._poll()
        with self.assertRaises(voting.NotEligible):
            voting.cast(poll, self.outsider, poll.choices.first())

    def test_leaving_the_room_revokes_answering(self):
        poll = self._poll()
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        with self.assertRaises(voting.NotEligible):
            voting.cast(poll, self.member_user, poll.choices.first())

    def test_a_closed_poll_refuses_an_answer(self):
        poll = self._poll()
        poll.close()
        with self.assertRaises(voting.NotOpen):
            voting.cast(poll, self.member_user, poll.choices.first())

    def test_a_poll_closes_when_its_clock_passes_with_nothing_running(self):
        """No sweep, no task, no cron: `is_open` is computed on read, so a
        deadline applies even when the worker is down. The consequence to
        remember is that `status` still reads 'open' in the database."""
        from datetime import timedelta

        poll = self._poll(closes_at=timezone.now() - timedelta(minutes=1))
        self.assertFalse(poll.is_open)
        self.assertEqual(poll.status, "open")
        with self.assertRaises(voting.NotOpen):
            voting.cast(poll, self.member_user, poll.choices.first())

    def test_a_final_poll_refuses_a_revision(self):
        poll = self._poll(revisability=Revisability.FINAL)
        pizza, sushi = poll.choices.all()
        voting.cast(poll, self.member_user, pizza)
        with self.assertRaises(voting.AlreadyAnswered):
            voting.cast(poll, self.member_user, sushi)

    def test_a_final_ballot_cannot_be_edited_even_around_the_service(self):
        """The rule lives on the model too, so a shell or an admin save
        cannot walk around the service that normally enforces it."""
        poll = self._poll(revisability=Revisability.FINAL)
        ballot = voting.cast(poll, self.member_user, poll.choices.first())
        with self.assertRaises(ValidationError):
            ballot.save()

    def test_answering_twice_the_same_way_is_not_a_revision(self):
        poll = self._poll()
        choice = poll.choices.first()
        voting.cast(poll, self.member_user, choice)
        voting.cast(poll, self.member_user, choice)
        self.assertEqual(PollBallot.objects.get().revisions, 0)


class TallyTests(RoomPollBase):
    def test_every_option_appears_including_the_unchosen(self):
        """A bar chart missing its empty bars misreports the shape of an
        opinion, and "no answers for this" is information."""
        poll = self._poll()
        voting.cast(poll, self.member_user, poll.choices.first())
        counted = voting.tally(poll)
        self.assertEqual([r.ballots for r in counted.results], [1, 0])
        self.assertEqual(counted.total_ballots, 1)

    def test_the_share_is_of_the_answers_not_of_the_room(self):
        poll = self._poll()
        voting.cast(poll, self.member_user, poll.choices.first())
        counted = voting.tally(poll)
        self.assertEqual(counted.share(counted.results[0]), 100.0)

    def test_the_audience_counts_active_members_only(self):
        poll = self._poll()
        self.assertEqual(voting.tally(poll).audience, 2)
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        self.assertEqual(voting.tally(poll).audience, 1)

    def test_the_tally_is_one_row_per_option_not_one_per_ballot(self):
        """The Meta-ordering GROUP BY trap: PollBallot orders by cast_at, and
        Django folds an ORDER BY column into the GROUP BY unless the query
        clears it."""
        poll = self._poll()
        voting.cast(poll, self.member_user, poll.choices.first())
        voting.cast(poll, self.staff, poll.choices.first())
        counted = voting.tally(poll)
        self.assertEqual(len(counted.results), 2)
        self.assertEqual(counted.results[0].ballots, 2)


class WithheldResultTests(RoomPollBase):
    def test_a_withheld_count_is_not_in_the_page_while_it_is_open(self):
        poll = self._poll(visibility=ResultVisibility.ON_CLOSE)
        voting.cast(poll, self.member_user, poll.choices.first())
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("forum:room_polls", args=[self.room.slug]))
        self.assertContains(response, "The count appears when this poll closes.")
        self.assertFalse(response.context["cards"][0]["results_visible"])

    def test_closing_it_reveals_the_count(self):
        poll = self._poll(visibility=ResultVisibility.ON_CLOSE)
        poll.close()
        self.client.force_login(self.member_user)
        response = self.client.get(
            reverse("forum:room_polls", args=[self.room.slug]))
        self.assertTrue(response.context["cards"][0]["results_visible"])


class ManageTests(RoomPollBase):
    def test_the_author_closes_their_own_poll(self):
        poll = self._poll()
        self.client.force_login(self.member_user)
        self.client.post(
            reverse("forum:room_poll_close", args=[self.room.slug, poll.slug]))
        poll.refresh_from_db()
        self.assertFalse(poll.is_open)
        self.assertIsNotNone(poll.closed_at)

    def test_closing_is_idempotent(self):
        poll = self._poll()
        first = poll.close().closed_at
        self.assertEqual(poll.close().closed_at, first)

    def test_staff_may_close_a_poll_they_did_not_open(self):
        poll = self._poll()
        self.assertTrue(voting.may_manage(poll, self.staff))

    def test_another_member_may_not_close_or_delete(self):
        from toto.people.models import Person

        other = User.objects.create_user(username="other", password="x")
        person = Person.objects.create(user=other, display_name="Other")
        ForumMember.objects.create(channel=self.room, person=person,
                                   is_active=True)
        poll = self._poll()
        self.assertFalse(voting.may_manage(poll, other))
        self.client.force_login(other)
        response = self.client.post(
            reverse("forum:room_poll_close", args=[self.room.slug, poll.slug]))
        self.assertEqual(response.status_code, 403)

    def test_deleting_a_poll_takes_its_answers(self):
        poll = self._poll()
        voting.cast(poll, self.member_user, poll.choices.first())
        self.client.force_login(self.member_user)
        self.client.post(
            reverse("forum:room_poll_delete", args=[self.room.slug, poll.slug]))
        self.assertEqual(RoomPoll.objects.count(), 0)
        self.assertEqual(PollBallot.objects.count(), 0)


class OptionParsingTests(TestCase):
    def test_a_label_and_its_text_split_on_the_first_colon(self):
        self.assertEqual(voting.parse_options("Pizza: the round one\nSushi"),
                         [("Pizza", "the round one"), ("Sushi", "")])

    def test_blank_lines_are_skipped_not_refused(self):
        self.assertEqual(len(voting.parse_options("A\n\n  \nB")), 2)

    def test_two_options_sharing_a_label_are_refused(self):
        with self.assertRaises(ValidationError):
            voting.parse_options("Pizza\npizza")

    def test_fewer_than_two_and_more_than_the_cap_are_refused(self):
        with self.assertRaises(ValidationError):
            voting.parse_options("Only one")
        with self.assertRaises(ValidationError):
            voting.parse_options("\n".join(f"o{n}" for n in range(20)))


class PageCostTests(RoomPollBase):
    def test_the_page_does_not_cost_a_query_per_poll(self):
        """It did: a tally aggregate, an audience count and a ballot lookup,
        each per card. Fine for the two polls a test makes; not fine for a
        room that has been running a year."""
        for n in range(6):
            self._poll(title=f"Question {n}")
        self.client.force_login(self.member_user)
        url = reverse("forum:room_polls", args=[self.room.slug])
        with self.assertNumQueries(self.count_for(url)):
            self.client.get(url)

    def count_for(self, url):
        """Measure the page with ONE poll, then assert six cost the same.

        Written as a measurement rather than a magic number so the test
        survives an unrelated query being added to the room chrome, and still
        fails the moment the cost starts scaling with the number of polls.

        The page is asked once before it is measured: a session's first
        request costs what its next ones do not — a host's "last seen" write
        on My account's list of sessions (toto.core's UserSessionMiddleware,
        at most one every few minutes) — and that write landed in the
        measurement and not in the measured request (2026-10-02, 41.4).
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        RoomPoll.objects.exclude(title="Question 0").delete()
        self.client.get(url)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(url)
        baseline = len(ctx)
        for n in range(1, 6):
            self._poll(title=f"Question {n}")
        return baseline


class SlugTests(RoomPollBase):
    def test_two_titles_that_reduce_to_nothing_do_not_collide(self):
        """slugify() answers "" for a title made only of punctuation or of a
        script it cannot transliterate. A bare fallback gave both the same
        slug, and the per-channel constraint turned that into a 500."""
        first = self._poll(title="???")
        second = self._poll(title="!!!")
        self.assertNotEqual(first.slug, second.slug)

    def test_a_poll_saved_straight_to_the_model_still_gets_a_free_slug(self):
        """The service de-duplicates; the model must too, or a shell or an
        admin save walks around it."""
        self._poll(title="Lunch?")
        direct = RoomPoll(channel=self.room, title="Lunch?",
                          question_text="Lunch?")
        direct.save()
        self.assertNotEqual(direct.slug, "lunch")
