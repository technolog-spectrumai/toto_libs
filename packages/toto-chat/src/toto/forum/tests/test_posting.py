"""Messages: posting, the op, removing, the feed and its cursor (stage 68).

    manage.py test toto.forum.tests.test_posting
"""

from unittest import mock

from toto.forum import billing, posting
from toto.forum.models import ForumChannel, ForumMessage
from toto.forum.testing import ForumCase, client_of, op, send_json, upload
from toto.vault.models import VaultFile


class PostTests(ForumCase):
    def test_a_message_is_posted_and_read(self):
        response = self.say(self.member, "  hello, Guild  ")
        self.assertEqual(response.status_code, 201)
        message = response.json()["message"]
        self.assertEqual(message["text"], "hello, Guild")
        self.assertEqual(message["sender"], "Mem")
        self.assertTrue(message["mine"])
        self.assertEqual(message["kind"], "text")
        self.assertIsNone(message["image"])
        row = ForumMessage.objects.get(pk=message["id"])
        self.assertEqual(row.text_bytes, len("hello, Guild"))
        self.assertEqual((row.number, row.seq), (1, 1))
        seen = self.feed(self.second).json()["messages"]
        self.assertEqual([m["text"] for m in seen], ["hello, Guild"])
        self.assertFalse(seen[0]["mine"])
        self.assertFalse(seen[0]["may_remove"])

    def test_text_bytes_are_utf8_bytes(self):
        row_id = self.say(self.member, "zażółć").json()["message"]["id"]
        self.assertEqual(ForumMessage.objects.get(pk=row_id).text_bytes,
                         len("zażółć".encode("utf-8")))

    def test_what_is_refused(self):
        client = client_of(self.member)
        url = self.url("post")
        self.assertEqual(client.post(url, {"op": op(), "text": "   "}).status_code, 400)
        self.assertEqual(client.post(url, {"text": "no op"}).status_code, 400)
        self.assertEqual(client.post(url, {"op": "not-a-uuid", "text": "x"}).status_code, 400)
        self.assertEqual(client.post(url, {"op": op(), "text": "nul\x00byte"}).status_code, 400)
        long = "x" * (posting.MAX_TEXT_BYTES + 1)
        self.assertEqual(client.post(url, {"op": op(), "text": long}).status_code, 413)
        self.assertEqual(ForumMessage.objects.count(), 0)

    def test_text_reaches_json_as_it_was_typed(self):
        text = '<script>alert(1)</script> & "quotes"'
        message = self.say(self.member, text).json()["message"]
        self.assertEqual(message["text"], text)

    def test_posting_is_rate_limited(self):
        with mock.patch.object(posting, "POSTS_PER_MINUTE", 2):
            self.assertEqual(self.say(self.member, "one").status_code, 201)
            self.assertEqual(self.say(self.member, "two").status_code, 201)
            self.assertEqual(self.say(self.member, "three").status_code, 429)
        self.assertEqual(ForumMessage.objects.count(), 2)


class OpTests(ForumCase):
    def test_a_retry_answers_the_stored_message(self):
        the_op = op()
        first = self.say(self.member, "once", the_op=the_op)
        again = self.say(self.member, "once", the_op=the_op)
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertTrue(again.json()["replay"])
        self.assertEqual(again.json()["message"]["id"], first.json()["message"]["id"])
        self.assertEqual(ForumMessage.objects.count(), 1)

    def test_the_same_op_for_another_request_is_409(self):
        the_op = op()
        self.assertEqual(self.say(self.member, "once", the_op=the_op).status_code, 201)
        self.assertEqual(self.say(self.member, "twice", the_op=the_op).status_code, 409)
        self.assertEqual(self.say(self.member, "once", image=upload(),
                                  the_op=the_op).status_code, 409)
        self.assertEqual(ForumMessage.objects.count(), 1)

    def test_an_op_is_one_members(self):
        the_op = op()
        self.assertEqual(self.say(self.member, "mine", the_op=the_op).status_code, 201)
        self.assertEqual(self.say(self.second, "mine", the_op=the_op).status_code, 201)
        self.assertEqual(ForumMessage.objects.count(), 2)

    def test_a_retry_with_an_image_stores_one_file(self):
        the_op = op()
        self.say(self.member, "pic", image=upload(), the_op=the_op)
        again = self.say(self.member, "pic", image=upload(), the_op=the_op)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(VaultFile.objects.filter(bucket=self.channel.bucket).count(), 1)


class SeamTests(ForumCase):
    """``billing.settle_post`` is where the next stage charges."""

    def test_the_seam_is_called_once_with_the_sizes(self):
        with mock.patch.object(billing, "settle_post") as settle:
            self.say(self.member, "zażółć", image=upload())
        settle.assert_called_once()
        args, kwargs = settle.call_args
        self.assertEqual(args[0], self.member)
        self.assertEqual(kwargs["text_bytes"], len("zażółć".encode("utf-8")))
        self.assertEqual(kwargs["image_bytes"], len(upload().read()))

    def test_a_retry_never_reaches_the_seam(self):
        the_op = op()
        with mock.patch.object(billing, "settle_post") as settle:
            self.say(self.member, "once", the_op=the_op)
            self.say(self.member, "once", the_op=the_op)
        self.assertEqual(settle.call_count, 1)

    def test_a_refusal_at_the_seam_leaves_no_message_and_no_image(self):
        class Refused(Exception):
            pass

        with mock.patch.object(billing, "settle_post", side_effect=Refused()):
            with self.assertRaises(Refused):
                self.say(self.member, "unpaid", image=upload())
        self.assertEqual(ForumMessage.objects.count(), 0)
        self.assertEqual(VaultFile.all_objects.filter(bucket=self.channel.bucket).count(), 0)
        self.assertEqual(ForumChannel.objects.get(pk=self.channel.pk).last_seq, 0)

    def test_a_post_refused_before_storing_never_reaches_the_seam(self):
        with mock.patch.object(billing, "settle_post") as settle:
            self.say(self.member, "   ")
            self.say(self.outsider, "not mine to post")
            self.say(self.member, "x", image=upload(b"<html>", "a.png"))
        settle.assert_not_called()


class RemoveTests(ForumCase):
    def test_removing_wipes_the_content_and_leaves_a_tombstone(self):
        message = self.say(self.member, "regret", image=upload()).json()["message"]
        response = send_json(client_of(self.member), self.url("message_remove", message["id"]))
        self.assertEqual(response.json()["message"],
                         {"id": message["id"], "number": 1, "seq": 2, "removed": True})
        row = ForumMessage.objects.get(pk=message["id"])
        self.assertIsNone(row.body_sealed)
        self.assertIsNone(row.attachment_id)
        self.assertEqual((row.text_bytes, row.attachment_mime, row.attachment_size),
                         (0, "", None))
        self.assertEqual(row.removed_by, self.member)
        self.assertEqual(VaultFile.all_objects.filter(bucket=self.channel.bucket).count(), 0)
        self.assertEqual(client_of(self.member).get(
            self.url("message_image", message["id"])).status_code, 404)

    def test_removing_twice_is_one_event(self):
        message = self.say(self.member, "regret").json()["message"]
        url = self.url("message_remove", message["id"])
        send_json(client_of(self.member), url)
        again = send_json(client_of(self.member), url)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["message"]["seq"], 2)
        self.assertEqual(ForumChannel.objects.get(pk=self.channel.pk).last_seq, 2)


class FeedTests(ForumCase):
    def test_the_first_feed_is_the_newest_page(self):
        for n in range(1, 6):
            self.say(self.member, f"m{n}")
        answer = self.feed(self.second, limit=3).json()
        self.assertEqual([m["text"] for m in answer["messages"]], ["m3", "m4", "m5"])
        self.assertEqual((answer["cursor"], answer["more"], answer["oldest"]), (5, True, 3))
        older = self.feed(self.second, before=answer["oldest"], limit=3).json()
        self.assertEqual([m["text"] for m in older["messages"]], ["m1", "m2"])
        self.assertEqual((older["more"], older["oldest"]), (False, 1))
        self.assertNotIn("cursor", older)

    def test_after_a_cursor_only_what_changed(self):
        self.say(self.member, "old")
        cursor = self.feed(self.second).json()["cursor"]
        nothing = self.feed(self.second, after=cursor).json()
        self.assertEqual((nothing["messages"], nothing["polls"], nothing["cursor"]),
                         ([], [], cursor))
        self.say(self.member, "new")
        poll = self.open_poll(self.member).json()["poll"]
        answer = self.feed(self.second, after=cursor).json()
        self.assertEqual([m["text"] for m in answer["messages"]], ["new"])
        self.assertEqual([p["id"] for p in answer["polls"]], [poll["id"]])
        self.assertEqual(answer["cursor"], cursor + 2)

    def test_a_removal_reaches_a_page_that_holds_the_message(self):
        message = self.say(self.member, "regret").json()["message"]
        cursor = self.feed(self.second).json()["cursor"]
        send_json(client_of(self.member), self.url("message_remove", message["id"]))
        answer = self.feed(self.second, after=cursor).json()
        self.assertEqual(answer["messages"],
                         [{"id": message["id"], "number": 1, "seq": 2, "removed": True}])
        # A page that starts now is not told of what it never held.
        self.assertEqual(self.feed(self.second).json()["messages"], [])

    def test_a_vote_and_a_close_reach_the_feed_as_the_poll(self):
        poll = self.open_poll(self.member).json()["poll"]
        cursor = self.feed(self.second).json()["cursor"]
        send_json(client_of(self.second), self.url("poll_vote", poll["id"]),
                  {"choice": poll["choices"][0]["id"]})
        answer = self.feed(self.member, after=cursor).json()
        self.assertEqual(answer["polls"][0]["total"], 1)
        self.assertEqual(answer["polls"][0]["choices"][0]["ballots"], 1)
        cursor = answer["cursor"]
        send_json(client_of(self.member), self.url("poll_remove", poll["id"]))
        answer = self.feed(self.second, after=cursor).json()
        self.assertEqual(answer["polls"], [{"id": poll["id"], "number": 1, "seq": answer["cursor"],
                                            "removed": True}])

    def test_a_long_absence_is_answered_in_pages(self):
        for n in range(1, 6):
            self.say(self.member, f"m{n}")
        first = self.feed(self.second, after=1, limit=2).json()
        self.assertEqual([m["text"] for m in first["messages"]], ["m2", "m3"])
        self.assertEqual((first["more"], first["cursor"]), (True, 3))
        rest = self.feed(self.second, after=first["cursor"], limit=2).json()
        self.assertEqual([m["text"] for m in rest["messages"]], ["m4", "m5"])
        self.assertEqual((rest["more"], rest["cursor"]), (False, 5))

    def test_bad_cursors_are_400(self):
        client = client_of(self.member)
        for query in ({"after": "x"}, {"before": "-1"}, {"after": "1e9"}):
            self.assertEqual(client.get(self.url("feed"), query).status_code, 400, query)

    def test_the_feed_says_what_a_cleanup_took(self):
        from django.utils import timezone

        self.assertIsNone(self.feed(self.member).json()["purged_before"])
        moment = timezone.now()
        ForumChannel.objects.filter(pk=self.channel.pk).update(purged_before=moment)
        self.assertEqual(self.feed(self.member, after=1).json()["purged_before"],
                         moment.isoformat())

    def test_one_channels_feed_holds_nothing_of_another(self):
        from toto.forum import channels

        channels.ensure_channel(self.other)
        self.say(self.outsider, "elsewhere", community=self.other)
        self.say(self.member, "here")
        self.assertEqual([m["text"] for m in self.feed(self.member).json()["messages"]],
                         ["here"])


class SearchTests(ForumCase):
    """The Search tab's door (the owner, 2026-10-07: "the 3rd tab is search -
    search through messages in this current forum")."""

    def search(self, user, q):
        return client_of(user).get(self.url("search"), {"q": q})

    def test_it_finds_by_a_word_and_by_a_sender_whatever_the_case(self):
        self.say(self.member, "The harbour is frozen")
        self.say(self.second, "skating tomorrow?")
        self.say(self.member, "HARBOUR master says no")
        found = self.search(self.second, "harbour").json()
        self.assertEqual([m["text"] for m in found["messages"]],
                         ["HARBOUR master says no", "The harbour is frozen"])     # the newest first
        self.assertEqual((found["query"], found["scanned"], found["capped"]), ("harbour", 3, False))
        self.assertEqual(len(self.search(self.member, "mem").json()["messages"]), 2)   # the sender's name
        self.assertEqual(self.search(self.member, "nothing like it").json()["messages"], [])

    def test_a_removed_message_is_not_found(self):
        row_id = self.say(self.member, "a secret word").json()["message"]["id"]
        send_json(client_of(self.member), self.url("message_remove", row_id), {})
        self.assertEqual(self.search(self.member, "secret").json()["messages"], [])

    def test_only_this_channel_is_searched(self):
        self.say(self.member, "only in the guild")
        found = client_of(self.admin).get(self.url("search", community=self.other), {"q": "guild"})
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()["messages"], [])
        # And a member of the Guild alone is not let into the other channel's search.
        self.assertEqual(client_of(self.member).get(self.url("search", community=self.other),
                                                    {"q": "guild"}).status_code, 403)

    def test_what_is_refused(self):
        self.say(self.member, "hello")
        self.assertEqual(self.search(self.member, "h").status_code, 400)
        self.assertEqual(self.search(self.member, "").status_code, 400)
        self.assertEqual(self.search(self.member, "x" * 101).status_code, 400)
        self.assertEqual(client_of(self.member).post(self.url("search"), {"q": "hello"}).status_code, 405)

    def test_a_search_writes_nothing_and_charges_nothing(self):
        from toto.forum.models import ForumUsageEvent

        self.say(self.member, "hello there")
        before = (ForumMessage.objects.count(), ForumUsageEvent.objects.count(),
                  ForumChannel.objects.get(community=self.guild).last_seq)
        with mock.patch.object(billing, "settle_post") as settle:
            self.assertEqual(self.search(self.member, "hello").status_code, 200)
        self.assertFalse(settle.called)
        self.assertEqual((ForumMessage.objects.count(), ForumUsageEvent.objects.count(),
                          ForumChannel.objects.get(community=self.guild).last_seq), before)

    def test_markup_in_a_message_comes_back_as_data(self):
        self.say(self.member, "<b>bold</b> <script>alert(1)</script>")
        response = self.search(self.member, "bold")
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")
        self.assertEqual(response.json()["messages"][0]["text"], "<b>bold</b> <script>alert(1)</script>")

    def test_the_hits_are_capped(self):
        with mock.patch.object(posting, "SEARCH_HITS", 2), mock.patch.object(posting, "POSTS_PER_MINUTE", 100):
            for index in range(4):
                self.say(self.member, f"same word {index}")
            found = self.search(self.member, "same word").json()
        self.assertEqual(len(found["messages"]), 2)
        self.assertTrue(found["capped"])


class ImageListTests(ForumCase):
    """The Images tab's door (the owner, 2026-10-07: "4th tab is images -
    which just list all images added to the forum")."""

    def images(self, user, **query):
        return client_of(user).get(self.url("image_list"), query)

    def test_it_lists_the_pictures_the_newest_first_and_no_plain_message(self):
        self.say(self.member, "no picture")
        first = self.say(self.member, "the harbour", image=upload()).json()["message"]["id"]
        second = self.say(self.second, "", image=upload()).json()["message"]["id"]
        listed = self.images(self.member).json()
        self.assertEqual([m["id"] for m in listed["messages"]], [second, first])
        self.assertFalse(listed["more"])
        for message in listed["messages"]:
            self.assertTrue(message["image"]["url"].endswith("/image/"))

    def test_a_removed_picture_is_gone_from_the_list(self):
        row_id = self.say(self.member, "x", image=upload()).json()["message"]["id"]
        send_json(client_of(self.member), self.url("message_remove", row_id), {})
        self.assertEqual(self.images(self.member).json()["messages"], [])

    def test_it_is_paged(self):
        ids = [self.say(self.member, f"p{index}", image=upload()).json()["message"]["id"]
               for index in range(3)]
        page = self.images(self.member, limit=2).json()
        self.assertEqual([m["id"] for m in page["messages"]], [ids[2], ids[1]])
        self.assertTrue(page["more"])
        rest = self.images(self.member, limit=2, before=page["oldest"]).json()
        self.assertEqual([m["id"] for m in rest["messages"]], [ids[0]])
        self.assertFalse(rest["more"])
