"""Polls, with the old forum's voting behaviour (stage 68, 2026-10-07).

The rules these state are the ones the old forum's ``test_polls_tab`` stated
— one member one answer, the order of the checks, a revision, a final
answer, the clock, results live or on close, who closes and removes — on
the new shape: a poll belongs to a community's channel and its question and
options are sealed.

    manage.py test toto.forum.tests.test_polls
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.utils import timezone

from toto.forum import channels, keys, voting
from toto.forum.models import (ChannelPoll, ForumChannel, PollBallot, PollChoice,
                               ResultVisibility, Revisability)
from toto.forum.testing import ForumCase, client_of, send_json


class PollCase(ForumCase):
    def make(self, user=None, **more):
        """A poll made through the service: ``(poll, [choices])``."""
        options = more.pop("options", "Soup\nSalad: with bread\nNothing")
        poll = voting.open_poll(self.channel, user or self.member, keys.open_key(self.channel),
                                title=more.pop("title", "Lunch?"), options=options, **more)
        return poll, list(poll.choices.all())

    def vote(self, user, poll, choice):
        return send_json(client_of(user), self.url("poll_vote", poll.id), {"choice": choice.pk})


class DoorTests(PollCase):
    def test_a_member_opens_a_poll(self):
        response = self.open_poll(self.member, title="Lunch?",
                                  options="Soup\n\nSalad: with bread\n")
        self.assertEqual(response.status_code, 201)
        poll = response.json()["poll"]
        self.assertEqual(poll["title"], "Lunch?")
        self.assertEqual([(c["label"], c["text"], c["ballots"]) for c in poll["choices"]],
                         [("Soup", "", 0), ("Salad", "with bread", 0)])
        self.assertEqual((poll["open"], poll["total"], poll["my_choice"]), (True, 0, None))
        self.assertEqual((poll["opener"], poll["mine"], poll["may_manage"]), ("Mem", True, True))
        row = ChannelPoll.objects.get(pk=poll["id"])
        self.assertEqual((row.channel_id, row.number, row.seq), (self.channel.pk, 1, 1))

    def test_options_may_be_a_list(self):
        response = self.open_poll(self.member, options=["Soup", "Salad: with bread"])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.json()["poll"]["choices"]), 2)

    def test_one_option_is_refused(self):
        self.assertEqual(self.open_poll(self.member, options="Only one").status_code, 400)
        self.assertEqual(self.open_poll(self.member, title="  ").status_code, 400)
        self.assertEqual(self.open_poll(self.member, revisability="sometimes").status_code, 400)
        self.assertEqual(self.open_poll(self.member, closes_at="yesterday").status_code, 400)
        past = (timezone.now() - timedelta(hours=1)).isoformat()
        self.assertEqual(self.open_poll(self.member, closes_at=past).status_code, 400)
        self.assertEqual(ChannelPoll.objects.count(), 0)

    def test_a_member_votes_and_revises(self):
        poll, (soup, salad, _nothing) = self.make()
        first = self.vote(self.second, poll, soup).json()["poll"]
        self.assertEqual((first["my_choice"], first["total"]), (soup.pk, 1))
        second = self.vote(self.second, poll, salad).json()["poll"]
        self.assertEqual((second["my_choice"], second["total"]), (salad.pk, 1))
        ballot = PollBallot.objects.get(poll=poll, voter=self.second)
        self.assertEqual((ballot.choice_id, ballot.revisions), (salad.pk, 1))
        self.assertIsNotNone(ballot.revised_at)

    def test_what_a_vote_is_refused_for(self):
        poll, (soup, _salad, _nothing) = self.make()
        other, (foreign, *_rest) = self.make(title="Dinner?")
        url = self.url("poll_vote", poll.id)
        client = client_of(self.second)
        self.assertEqual(send_json(client, url, {"choice": foreign.pk}).status_code, 400)
        self.assertEqual(send_json(client, url, {"choice": "soup"}).status_code, 400)
        self.assertEqual(send_json(client, url, {}).status_code, 400)
        self.assertEqual(send_json(client, self.url("poll_vote", ChannelPoll().id),
                                   {"choice": soup.pk}).status_code, 404)
        self.assertEqual(PollBallot.objects.count(), 0)

    def test_a_poll_of_another_channel_never_lists_here(self):
        channels.ensure_channel(self.other)
        send_json(client_of(self.outsider), self.url("poll_open", community=self.other),
                  {"title": "Theirs", "options": "a\nb"})
        self.make()
        self.assertEqual([p["title"] for p in self.feed(self.member).json()["polls"]],
                         ["Lunch?"])


class AnswerRuleTests(PollCase):
    def test_the_engine_refuses_who_may_not_read_directly(self):
        poll, (soup, *_rest) = self.make()
        for user in (self.outsider, self.free, self.staff):
            with self.assertRaises(voting.NotEligible):
                voting.cast(poll, user, soup)

    def test_leaving_the_community_revokes_answering(self):
        poll, (soup, *_rest) = self.make()
        self.second_person.communities.remove(self.guild)
        with self.assertRaises(voting.NotEligible):
            voting.cast(poll, self.second, soup)

    def test_the_order_of_the_checks(self):
        """Closed is said before "not yours", and "not yours" before "not
        allowed": the order somebody disputes afterwards."""
        poll, (soup, *_rest) = self.make()
        other, (foreign, *_r) = self.make(title="Dinner?")
        with self.assertRaises(voting.UnknownChoice):
            voting.cast(poll, self.outsider, foreign)
        voting.close_poll(poll)
        with self.assertRaises(voting.NotOpen):
            voting.cast(poll, self.outsider, foreign)

    def test_a_closed_poll_refuses_an_answer(self):
        poll, (soup, *_rest) = self.make()
        voting.close_poll(poll)
        self.assertEqual(self.vote(self.second, poll, soup).status_code, 409)
        self.assertEqual(PollBallot.objects.count(), 0)

    def test_a_poll_closes_when_its_clock_passes_with_nothing_running(self):
        poll, (soup, *_rest) = self.make(closes_at=timezone.now() + timedelta(hours=1))
        self.assertTrue(poll.is_open)
        ChannelPoll.objects.filter(pk=poll.pk).update(
            closes_at=timezone.now() - timedelta(seconds=1))
        poll.refresh_from_db()
        self.assertEqual(poll.status, "open")       # the column still says open
        self.assertFalse(poll.is_open)              # the clock does not
        with self.assertRaises(voting.NotOpen):
            voting.cast(poll, self.second, soup)

    def test_a_final_poll_refuses_a_revision(self):
        poll, (soup, salad, _nothing) = self.make(revisability=Revisability.FINAL)
        self.assertEqual(self.vote(self.second, poll, soup).status_code, 200)
        self.assertEqual(self.vote(self.second, poll, salad).status_code, 409)
        self.assertEqual(PollBallot.objects.get(poll=poll, voter=self.second).choice_id,
                         soup.pk)

    def test_a_final_ballot_cannot_be_edited_even_around_the_service(self):
        poll, (soup, salad, _nothing) = self.make(revisability=Revisability.FINAL)
        ballot = voting.cast(poll, self.second, soup)
        ballot.choice = salad
        with self.assertRaises(ValidationError):
            ballot.save()
        with self.assertRaises(ValidationError):
            ballot.delete()

    def test_answering_twice_the_same_way_is_not_a_revision(self):
        poll, (soup, *_rest) = self.make()
        voting.cast(poll, self.second, soup)
        seq = ChannelPoll.objects.get(pk=poll.pk).seq
        again = voting.cast(poll, self.second, soup)
        self.assertEqual(again.revisions, 0)
        self.assertEqual(PollBallot.objects.count(), 1)
        self.assertEqual(ChannelPoll.objects.get(pk=poll.pk).seq, seq)

    def test_one_member_one_answer(self):
        poll, (soup, salad, _nothing) = self.make()
        voting.cast(poll, self.second, soup)
        voting.cast(poll, self.second, salad)
        voting.cast(poll, self.head, soup)
        self.assertEqual(PollBallot.objects.filter(poll=poll).count(), 2)


class TallyTests(PollCase):
    def test_every_option_appears_including_the_unchosen(self):
        poll, (soup, salad, nothing) = self.make()
        voting.cast(poll, self.second, soup)
        voting.cast(poll, self.head, soup)
        tally = voting.tally(poll, keys.open_key(self.channel))
        self.assertEqual([(r.label, r.text, r.ballots) for r in tally.results],
                         [("Soup", "", 2), ("Salad", "with bread", 0), ("Nothing", "", 0)])
        self.assertEqual(tally.total_ballots, 2)

    def test_the_share_is_of_the_answers_not_of_the_community(self):
        poll, (soup, salad, _nothing) = self.make()
        voting.cast(poll, self.second, soup)
        voting.cast(poll, self.head, salad)
        voting.cast(poll, self.senior, salad)
        tally = voting.tally(poll, keys.open_key(self.channel))
        self.assertEqual([tally.share(r) for r in tally.results], [33.3, 66.7, 0.0])

    def test_an_empty_poll_has_no_share(self):
        poll, _choices = self.make()
        tally = voting.tally(poll, keys.open_key(self.channel))
        self.assertEqual([tally.share(r) for r in tally.results], [0.0, 0.0, 0.0])

    def test_the_list_does_not_cost_a_query_per_poll(self):
        for n in range(4):
            poll, (soup, *_rest) = self.make(title=f"Q{n}?")
            voting.cast(poll, self.second, soup)
        polls = list(self.channel.polls.all())
        key = keys.open_key(self.channel)
        with self.assertNumQueries(3):
            voting.polls_to_dicts(polls, key=key, user=self.second)


class WithheldResultTests(PollCase):
    def test_a_withheld_count_is_not_in_the_answer_while_it_is_open(self):
        poll, (soup, *_rest) = self.make(visibility=ResultVisibility.ON_CLOSE)
        answer = self.vote(self.second, poll, soup).json()["poll"]
        self.assertFalse(answer["results_visible"])
        self.assertIsNone(answer["total"])
        self.assertEqual([c["ballots"] for c in answer["choices"]], [None, None, None])
        self.assertEqual(answer["my_choice"], soup.pk)

    def test_closing_it_reveals_the_count(self):
        poll, (soup, *_rest) = self.make(visibility=ResultVisibility.ON_CLOSE)
        self.vote(self.second, poll, soup)
        answer = send_json(client_of(self.member), self.url("poll_close", poll.id)).json()["poll"]
        self.assertTrue(answer["results_visible"])
        self.assertEqual((answer["total"], answer["choices"][0]["ballots"]), (1, 1))
        self.assertFalse(answer["open"])


class ManageTests(PollCase):
    def test_the_opener_closes_their_own_poll(self):
        poll, _choices = self.make()
        response = send_json(client_of(self.member), self.url("poll_close", poll.id))
        self.assertEqual(response.status_code, 200)
        poll.refresh_from_db()
        self.assertEqual(poll.status, "closed")
        self.assertIsNotNone(poll.closed_at)

    def test_closing_is_idempotent(self):
        poll, _choices = self.make()
        self.assertTrue(voting.close_poll(poll))
        seq = ChannelPoll.objects.get(pk=poll.pk).seq
        self.assertFalse(voting.close_poll(poll))
        self.assertEqual(ChannelPoll.objects.get(pk=poll.pk).seq, seq)

    def test_removing_a_poll_takes_its_question_options_and_answers(self):
        poll, (soup, *_rest) = self.make(revisability=Revisability.FINAL)
        voting.cast(poll, self.second, soup)
        response = send_json(client_of(self.head), self.url("poll_remove", poll.id))
        self.assertEqual(response.json()["poll"]["removed"], True)
        poll.refresh_from_db()
        self.assertIsNone(poll.title_sealed)
        self.assertEqual(poll.removed_by, self.head)
        self.assertEqual(PollChoice.objects.filter(poll=poll).count(), 0)
        self.assertEqual(PollBallot.objects.filter(poll=poll).count(), 0)
        self.assertEqual(self.feed(self.member).json()["polls"], [])
        self.assertEqual(send_json(client_of(self.head),
                                   self.url("poll_remove", poll.id)).status_code, 404)

    def test_deleting_a_community_takes_its_channel_polls_and_answers(self):
        poll, (soup, *_rest) = self.make()
        voting.cast(poll, self.second, soup)
        self.guild.delete()
        self.assertEqual(ForumChannel.objects.filter(pk=self.channel.pk).count(), 0)
        self.assertEqual(ChannelPoll.objects.count(), 0)
        self.assertEqual(PollBallot.objects.count(), 0)


class OptionParsingTests(SimpleTestCase):
    def test_a_label_and_its_text_split_on_the_first_colon(self):
        self.assertEqual(voting.parse_options("Tea: green: hot\nCoffee"),
                         [("Tea", "green: hot"), ("Coffee", "")])

    def test_blank_lines_are_skipped_not_refused(self):
        self.assertEqual(len(voting.parse_options("\nA\n\n  \nB\n")), 2)

    def test_two_options_sharing_a_label_are_refused(self):
        with self.assertRaises(ValidationError):
            voting.parse_options("Tea\ntea: again")

    def test_fewer_than_two_and_more_than_the_cap_are_refused(self):
        with self.assertRaises(ValidationError):
            voting.parse_options("Only")
        with self.assertRaises(ValidationError):
            voting.parse_options("\n".join(f"o{n}" for n in range(voting.MAX_OPTIONS + 1)))
        self.assertEqual(len(voting.parse_options(
            "\n".join(f"o{n}" for n in range(voting.MAX_OPTIONS)))), voting.MAX_OPTIONS)
