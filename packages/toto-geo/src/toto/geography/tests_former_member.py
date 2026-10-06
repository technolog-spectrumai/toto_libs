"""After leaving a community (stage 64, 2026-10-06) a member keeps one right:
to delete their own pins and zones and withdraw their own comments, from the
list of their own rows. Everything else about the community is 403.

    manage.py test toto.geography.tests_former_member
"""

from django.urls import reverse

from toto.comments.models import Comment
from toto.geography.models import CommunityPin, CommunityZone
from toto.geography.testing import client_of, op, post
from toto.geography.testing_locations import PIN, LocationsCase


class FormerMemberTests(LocationsCase):
    def setUp(self):
        super().setUp()
        self.row = self.pin()
        self.area = self.zone()
        self.assertEqual(self.comment(self.member_user, self.row, "Mine").status_code, 200)
        self.heads_pin = self.pin(self.head_user, name="Head's")
        self.member.communities.remove(self.guild)          # mia leaves
        self.client_ = client_of(self.member_user)
        self.list_url = reverse("geography:my_contributions")

    def test_the_community_s_doors_are_403_now(self):
        self.assertEqual(self.client_.get(self.url("pin_detail", self.row)).status_code, 403)
        self.assertEqual(post(self.client_, self.url("pin_detail", self.row),
                              {**PIN, "name": "Edited", "op": op()}).status_code, 403)
        self.assertEqual(post(self.client_, self.url("pin_delete", self.row), {}).status_code,
                         403)
        self.assertEqual(self.save_pin().status_code, 403)
        self.assertEqual(self.comment(self.member_user, self.row, "Again").status_code, 403)
        page = self.client_.get(self.page_url).content.decode()
        self.assertNotIn(str(self.row.uid), page)
        self.assertNotIn(str(self.heads_pin.uid), page)

    def test_the_list_holds_their_own_rows_and_nobody_else_s(self):
        text = self.client_.get(self.list_url).content.decode()
        for mine in ("Well", "Meadow", "Mine", "Guild"):
            self.assertIn(mine, text)
        self.assertNotIn("Head&#x27;s", text)
        self.assertNotIn("leaflet", text)

    def test_they_delete_their_own_pin_and_zone_from_the_list(self):
        response = self.client_.post(self.url("my_pin_delete", self.row))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CommunityPin.objects.filter(pk=self.row.pk).exists())
        self.assertEqual(self.client_.post(self.url("my_zone_delete", self.area)).status_code,
                         302)
        self.assertFalse(CommunityZone.objects.exists())
        # Somebody else's row is not theirs to delete from the list.
        self.assertEqual(self.client_.post(self.url("my_pin_delete", self.heads_pin)).status_code,
                         404)
        self.assertTrue(CommunityPin.objects.filter(pk=self.heads_pin.pk).exists())

    def test_they_withdraw_their_own_comment_from_the_list(self):
        comment = Comment.objects.get(body="Mine")
        url = reverse("geography:my_comment_withdraw", kwargs={"pk": comment.pk})
        self.assertEqual(client_of(self.head_user).post(url).status_code, 404)   # not theirs
        self.assertEqual(self.client_.post(url).status_code, 302)
        comment.refresh_from_db()
        self.assertTrue(comment.is_deleted)
        self.assertEqual(self.client_.get(url).status_code, 405)
