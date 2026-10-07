"""The two pages, the seed, an erased member and the copy of one's data
(stage 68, 2026-10-07).

    manage.py test toto.forum.tests.test_pages
"""

import json
import re
from io import StringIO

from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import override_settings

from toto.forum import channels, erasure
from toto.forum.models import ChannelPoll, ForumChannel, ForumMessage, ForumSettings
from toto.forum.testing import ForumCase, client_of, member, on_plan, restart, send_json, upload
from toto.socialhub.models import Community
from toto.vault.models import VaultFile

CONFIG = re.compile(
    r'<script id="forum-channel-config" type="application/json">(.*?)</script>', re.S)


def config_of(response):
    return json.loads(CONFIG.search(response.content.decode()).group(1))


class ListPageTests(ForumCase):
    def test_a_member_sees_their_communities_channels(self):
        page = client_of(self.member).get("/forum/").content.decode()
        self.assertIn(self.url("channel_detail"), page)
        self.assertNotIn(self.url("channel_detail", community=self.other), page)

    def test_an_administrator_sees_every_community(self):
        page = client_of(self.admin).get("/forum/").content.decode()
        self.assertIn(self.url("channel_detail"), page)
        self.assertIn(self.url("channel_detail", community=self.other), page)

    def test_a_member_of_no_community_is_told_so(self):
        loner, _person = member("loner")
        on_plan(loner)
        page = client_of(loner).get("/forum/").content.decode()
        self.assertIn('data-testid="forum-no-channels"', page)

    def test_a_communitys_name_is_text(self):
        Community.objects.filter(pk=self.guild.pk).update(name="<b>Guild</b> & <script>x</script>")
        page = client_of(self.member).get("/forum/").content.decode()
        self.assertNotIn("<script>x</script>", page)
        self.assertIn("&lt;b&gt;Guild&lt;/b&gt;", page)


class ChannelPageTests(ForumCase):
    def test_the_page_carries_its_data_and_its_script(self):
        self.say(self.member, "the first word")
        self.open_poll(self.member)
        response = client_of(self.second).get(self.url("channel_detail"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        page = response.content.decode()
        self.assertIn("forum/channel.js", page)
        self.assertIn("data-forum-channel", page)
        config = config_of(response)
        self.assertEqual(config["community"], {"name": "Guild", "slug": self.guild.slug})
        self.assertEqual(config["viewer"], {"name": "Second", "may_moderate": False})
        self.assertEqual(config["urls"]["feed"], self.url("feed"))
        self.assertEqual(config["urls"]["post"], self.url("post"))
        self.assertIn(config["urls"]["nil"], config["urls"]["message_remove"])
        self.assertEqual([m["text"] for m in config["feed"]["messages"]], ["the first word"])
        self.assertEqual(len(config["feed"]["polls"]), 1)
        self.assertEqual(config["feed"]["cursor"], 2)
        self.assertEqual(config["refresh_seconds"], ForumSettings.current().refresh_seconds)
        self.assertEqual(config["limits"]["image_types"],
                         ["image/gif", "image/jpeg", "image/png", "image/webp"])

    def test_the_head_and_an_administrator_are_told_they_moderate(self):
        for user in (self.head, self.admin):
            config = config_of(client_of(user).get(self.url("channel_detail")))
            self.assertTrue(config["viewer"]["may_moderate"], user.username)
            self.say(self.member, "word")
            config = config_of(client_of(user).get(self.url("channel_detail")))
            self.assertTrue(config["feed"]["messages"][0]["may_remove"])

    def test_what_members_wrote_reaches_the_page_only_as_data(self):
        text = '</script><img src=x onerror=alert(1)> "q"'
        self.say(self.member, text)
        self.open_poll(self.member, title="<i>Q</i>?", options="<b>a</b>\n<u>b</u>: <s>t</s>")
        response = client_of(self.second).get(self.url("channel_detail"))
        page = response.content.decode()
        for markup in ("<img src=x", "<i>Q</i>", "<b>a</b>", "<s>t</s>", "</script><img"):
            self.assertNotIn(markup, page)
        config = config_of(response)
        self.assertEqual(config["feed"]["messages"][0]["text"], text)
        self.assertEqual(config["feed"]["polls"][0]["title"], "<i>Q</i>?")

    def test_the_first_opening_makes_the_channel(self):
        self.assertFalse(ForumChannel.objects.filter(community=self.other).exists())
        response = client_of(self.outsider).get(self.url("channel_detail", community=self.other))
        self.assertEqual(response.status_code, 200)
        made = ForumChannel.objects.get(community=self.other)
        self.assertIsNotNone(made.bucket_id)
        self.assertTrue(hasattr(made, "channel_key"))
        client_of(self.outsider).get(self.url("channel_detail", community=self.other))
        self.assertEqual(ForumChannel.objects.filter(community=self.other).count(), 1)

    def test_a_refusal_is_a_plain_page_with_its_status(self):
        response = client_of(self.outsider).get(self.url("channel_detail"))
        self.assertEqual(response.status_code, 403)
        self.assertIn('data-testid="forum-refused"', response.content.decode())

    def test_the_script_writes_text_and_opens_no_socket(self):
        source = open(finders.find("forum/channel.js"), encoding="utf-8").read()
        code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        for word in ("innerHTML", "insertAdjacentHTML", "document.write", "WebSocket",
                     "EventSource", "setInterval", "eval("):
            self.assertNotIn(word, code, word)
        self.assertIn("textContent", code)


class SeedTests(ForumCase):
    def test_ingress_forum_makes_a_channel_for_every_community_once(self):
        Community.objects.create(name="Third")
        out = StringIO()
        call_command("ingress_forum", stdout=out)
        self.assertEqual(ForumChannel.objects.count(), 3)
        self.assertIn("2 made now", out.getvalue())
        out = StringIO()
        call_command("ingress_forum", stdout=out)
        self.assertEqual(ForumChannel.objects.count(), 3)
        self.assertIn("0 made now", out.getvalue())
        for channel in ForumChannel.objects.all():
            self.assertIsNotNone(channel.bucket_id)
            self.assertTrue(hasattr(channel, "channel_key"))

    def test_without_the_secret_it_makes_nothing_and_says_so(self):
        restart()
        out = StringIO()
        with override_settings(FORUM_VAULT_PASSWORD=""):
            call_command("ingress_forum", stdout=out)
            restart()
        self.assertEqual(ForumChannel.objects.count(), 1)
        self.assertIn("Forum channels not made", out.getvalue())

    def test_ingress_mode_none_seeds_nothing(self):
        call_command("ingress_forum", mode="none", stdout=StringIO())
        self.assertEqual(ForumChannel.objects.count(), 1)


class ErasedMemberTests(ForumCase):
    def test_text_stays_under_a_neutral_label_and_pictures_go(self):
        words = self.say(self.member, "my words").json()["message"]
        both = self.say(self.member, "caption", image=upload()).json()["message"]
        bare = self.say(self.member, "", image=upload()).json()["message"]
        poll = self.open_poll(self.member).json()["poll"]
        self.assertEqual(erasure.sent_by(self.member), {"messages": 3, "attachments": 2})

        self.assertEqual(erasure.forget_sender(self.member), [])
        erasure.delete_blobs([])
        label = erasure.former_member_label()
        seen = {m["id"]: m for m in self.feed(self.second).json()["messages"]}
        self.assertEqual((seen[words["id"]]["text"], seen[words["id"]]["sender"]),
                         ("my words", label))
        self.assertEqual((seen[both["id"]]["text"], seen[both["id"]]["image"],
                          seen[both["id"]]["kind"]), ("caption", None, "text"))
        self.assertNotIn(bare["id"], seen)
        self.assertIsNotNone(ForumMessage.objects.get(pk=bare["id"]).removed_at)
        self.assertEqual(VaultFile.all_objects.filter(bucket=self.channel.bucket).count(), 0)
        self.assertEqual(ChannelPoll.objects.get(pk=poll["id"]).opener_name, label)

    def test_the_rows_outlive_the_account(self):
        words = self.say(self.member, "my words").json()["message"]
        erasure.forget_sender(self.member)
        self.member.delete()
        row = ForumMessage.objects.get(pk=words["id"])
        self.assertIsNone(row.sender_id)
        self.assertEqual(self.feed(self.second).json()["messages"][0]["text"], "my words")


class PersonalDataTests(ForumCase):
    def test_a_members_copy_holds_their_own_messages_with_their_text(self):
        from toto.core.personal_data import _forum

        self.say(self.member, "mine to keep")
        self.say(self.member, "with a picture", image=upload())
        gone = self.say(self.member, "regret").json()["message"]
        send_json(client_of(self.member), self.url("message_remove", gone["id"]))
        self.say(self.second, "somebody else's")
        (table,) = _forum(self.member)
        self.assertEqual(table.name, "forum_messages")
        self.assertEqual([(row["text"], row["community"], row["image"]) for row in table.rows],
                         [("mine to keep", "Guild", False), ("with a picture", "Guild", True)])


class CommunityPanelTests(ForumCase):
    """The community page's way into the channel (toto.socialhub)."""

    def panel(self, user, community=None):
        from django.urls import reverse

        community = community or self.guild
        page = client_of(user).get(reverse("socialhub:community_detail",
                                           args=[community.slug])).content.decode()
        return 'data-testid="community-forum"' in page, page

    def test_who_may_read_the_channel_sees_the_way_in(self):
        for user in (self.member, self.senior, self.head, self.admin):
            shown, page = self.panel(user)
            self.assertTrue(shown, user.username)
            self.assertIn(self.url("channel_detail"), page)

    def test_nobody_else_sees_it(self):
        for user in (self.free, self.outsider, self.staff):
            shown, _page = self.panel(user)
            self.assertFalse(shown, user.username)
