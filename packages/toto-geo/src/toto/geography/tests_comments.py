"""Comments under community pins and zones (stage 64, 2026-10-06): written
by who may see the row, charged ``geography.comment`` once; edited by their
author alone, free; withdrawn by their author or a moderator. Plain staff
gets neither through any door. A reply answers a top-level comment only.

    manage.py test toto.geography.tests_comments
"""

from toto.comments.models import Comment
from toto.geography.models import PinComment, ZoneComment
from toto.geography.testing import client_of, no_funds, op, refusing_ledger
from toto.geography.testing_locations import LocationsCase


class CommentCase(LocationsCase):
    def setUp(self):
        super().setUp()
        self.row = self.pin()
        self.area = self.zone()

    def written(self, user=None, body="Seen it"):
        response = self.comment(user or self.member_user, self.row, body)
        self.assertEqual(response.status_code, 200, response.content)
        return Comment.objects.order_by("-pk").first()


class WriteTests(CommentCase):
    def test_a_comment_is_charged_once_and_tied_to_its_row(self):
        response = self.comment(self.senior_user, self.row, "  Is it deep?  ")
        self.assertEqual(response.json(), {"ok": True, "charged": True})
        link = PinComment.objects.get()
        self.assertEqual((link.pin, link.comment.body, link.comment.author),
                         (self.row, "Is it deep?", self.senior_user))
        self.assertEqual(self.events("geography.comment").count(), 1)
        under_zone = self.comment(self.head_user, self.area, "Ours", name="zone_comment_add")
        self.assertEqual(under_zone.status_code, 200)
        self.assertEqual(ZoneComment.objects.get().zone, self.area)

    def test_a_retry_is_one_comment_and_one_charge(self):
        key = op()
        client = client_of(self.member_user)
        url = self.url("pin_comment_add", self.row)
        first = client.post(url, {"body": "Once", "op": key})
        second = client.post(url, {"body": "Once", "op": key})
        self.assertEqual(first.json()["charged"], True)
        self.assertEqual(second.json(), {"ok": True, "charged": False})
        self.assertEqual(Comment.objects.count(), 1)
        self.assertEqual(self.events("geography.comment").count(), 1)
        self.assertEqual(client.post(url, {"body": "Twice", "op": key}).status_code, 409)

    def test_refusals_write_and_charge_nothing(self):
        client = client_of(self.member_user)
        url = self.url("pin_comment_add", self.row)
        self.assertEqual(client.post(url, {"body": "   ", "op": op()}).status_code, 400)
        self.assertEqual(client.post(url, {"body": "no op"}).status_code, 400)
        self.assertEqual(client.post(url, {"body": "x" * 8001, "op": op()}).status_code, 400)
        self.assertEqual(client.get(url).status_code, 405)
        with no_funds():
            self.assertEqual(client.post(url, {"body": "poor", "op": op()}).status_code, 402)
        with refusing_ledger():
            self.assertEqual(client.post(url, {"body": "poor", "op": op()}).status_code, 402)
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(self.events("geography.comment").exists())

    def test_a_reply_answers_a_top_level_comment_of_the_same_row(self):
        top = self.written()
        reply = self.comment(self.head_user, self.row, "Yes", reply_to=top.pk)
        self.assertEqual(reply.status_code, 200)
        answer = Comment.objects.get(body="Yes")
        self.assertEqual(answer.reply_to, top)
        before = self.events("geography.comment").count()
        # A reply to a reply is refused, and charges nothing.
        self.assertEqual(self.comment(self.member_user, self.row, "Deeper",
                                      reply_to=answer.pk).status_code, 400)
        # A comment of another row is no parent here.
        other = self.comment(self.member_user, self.area, "Elsewhere", name="zone_comment_add")
        self.assertEqual(other.status_code, 200)
        elsewhere = Comment.objects.get(body="Elsewhere")
        self.assertEqual(self.comment(self.member_user, self.row, "Crossed",
                                      reply_to=elsewhere.pk).status_code, 404)
        self.assertEqual(self.events("geography.comment").count(), before + 1)
        self.assertFalse(Comment.objects.filter(body__in=("Deeper", "Crossed")).exists())


class EditAndWithdrawTests(CommentCase):
    def test_the_author_edits_free(self):
        comment = self.written()
        url = self.url("pin_comment_edit", self.row, pk=comment.pk)
        response = client_of(self.member_user).post(url, {"body": "Seen it twice"})
        self.assertEqual(response.json(), {"ok": True})
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Seen it twice")
        self.assertIsNotNone(comment.edited_at)
        self.assertEqual(self.events("geography.comment").count(), 1)

    def test_nobody_else_edits_not_staff_not_the_head_not_an_administrator(self):
        comment = self.written()
        url = self.url("pin_comment_edit", self.row, pk=comment.pk)
        for user in (self.head_user, self.senior_user, self.root, self.staff_user):
            with self.subTest(user=user.username):
                self.assertEqual(client_of(user).post(url, {"body": "Theirs"}).status_code, 403)
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Seen it")

    def test_the_author_and_the_moderators_withdraw(self):
        for user in (self.member_user, self.head_user, self.root):
            with self.subTest(user=user.username):
                comment = self.written(self.member_user, f"by {user.username}")
                url = self.url("pin_comment_withdraw", self.row, pk=comment.pk)
                self.assertEqual(client_of(user).post(url).status_code, 200)
                comment.refresh_from_db()
                self.assertTrue(comment.is_deleted)
                self.assertEqual(comment.body, f"by {user.username}")     # kept, unshown

    def test_a_senior_member_and_plain_staff_do_not_withdraw(self):
        comment = self.written()
        url = self.url("pin_comment_withdraw", self.row, pk=comment.pk)
        self.assertEqual(client_of(self.senior_user).post(url).status_code, 403)
        self.assertEqual(client_of(self.staff_user).post(url).status_code, 403)   # no member
        self.guild.senior_members.add(self._person(self.staff_user))
        # Staff who belongs to the community is a member like any other.
        self.assertEqual(client_of(self.staff_user).post(url).status_code, 403)
        self.assertEqual(client_of(self.staff_user).post(
            self.url("pin_comment_edit", self.row, pk=comment.pk), {"body": "x"}).status_code,
            403)
        comment.refresh_from_db()
        self.assertFalse(comment.is_deleted)

    def _person(self, user):
        from toto.people.models import Person

        return Person.objects.get(user=user)

    def test_a_comment_of_another_row_is_404_under_this_one(self):
        comment = self.written()
        url = self.url("zone_comment_withdraw", self.area, pk=comment.pk)
        self.assertEqual(client_of(self.member_user).post(url).status_code, 404)

    def test_the_thread_in_the_details(self):
        comment = self.written(self.member_user, "<b>bold</b> words")
        text = client_of(self.head_user).get(self.url("pin_detail", self.row)).content.decode()
        self.assertIn("&lt;b&gt;bold&lt;/b&gt; words", text)
        self.assertNotIn("<b>bold</b>", text)
        self.assertIn('name="op"', text)
        self.assertNotIn(self.url("pin_comment_edit", self.row, pk=comment.pk), text)
        self.assertIn(self.url("pin_comment_withdraw", self.row, pk=comment.pk), text)
        own = client_of(self.member_user).get(self.url("pin_detail", self.row)).content.decode()
        self.assertIn(self.url("pin_comment_edit", self.row, pk=comment.pk), own)
        senior = client_of(self.senior_user).get(self.url("pin_detail", self.row)).content.decode()
        self.assertNotIn(self.url("pin_comment_withdraw", self.row, pk=comment.pk), senior)

    def test_comments_go_with_their_row(self):
        self.written()
        self.comment(self.member_user, self.area, "Under the zone", name="zone_comment_add")
        self.row.delete()
        self.area.delete()
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(PinComment.objects.exists())
