"""The two pages, the seed, an erased member and the copy of one's data
(stage 68, 2026-10-07; the pages are live since stage 70 and sit in the
platform's frame).

    manage.py test toto.forum.tests.test_pages
"""

import json
import re
from io import StringIO

from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import override_settings
from django.urls import NoReverseMatch, reverse

from toto.forum import channels, erasure
from toto.forum.models import ChannelPoll, ForumChannel, ForumMessage, ForumSettings
from toto.forum.testing import ForumCase, client_of, member, on_plan, restart, send_json, upload
from toto.socialhub.models import Community
from toto.vault.models import VaultFile

CONFIG = re.compile(
    r'<script id="forum-channel-config" type="application/json">(.*?)</script>', re.S)


def config_of(response):
    return json.loads(CONFIG.search(response.content.decode()).group(1))


def settings_url():
    """The forum's Settings page, where this tree has one (stage 69)."""
    try:
        return reverse("forum:settings")
    except NoReverseMatch:
        return None


def part(page, testid, tag):
    """The element of ``page`` that carries ``data-testid`` (its markup)."""
    found = re.search(rf'<{tag}\b[^>]*data-testid="{testid}".*?</{tag}>', page, re.S)
    return found.group(0) if found else None


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

    def test_each_entry_is_one_link_that_enters_the_channel(self):
        """The whole row is the link, in this tab, with the community's kind
        beside its name; the list page loads no script of the forum's."""
        Community.objects.filter(pk=self.guild.pk).update(org_type=Community.COMPANY)
        page = client_of(self.member).get("/forum/").content.decode()
        rows = re.findall(r'<a\b[^>]*data-testid="forum-channel-link".*?</a>', page, re.S)
        self.assertEqual(len(rows), 1)
        self.assertIn(f'href="{self.url("channel_detail")}"', rows[0])
        self.assertNotIn("target=", rows[0])
        self.assertIn("Guild", rows[0])
        self.assertIn(str(Community.objects.get(pk=self.guild.pk).get_org_type_display()), rows[0])
        self.assertNotIn("forum/channel.js", page)

    def test_the_settings_link_is_an_administrators(self):
        address = settings_url()
        for user, shown in ((self.admin, True), (self.head, False), (self.member, False),
                            (self.staff, False)):
            page = client_of(user).get("/forum/").content.decode()
            self.assertEqual('data-testid="forum-settings-link"' in page,
                             bool(shown and address), user.username)


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

    def test_the_column_lists_the_members_channels_and_marks_the_open_one(self):
        third = Community.objects.create(name="Third")
        self.member_person.communities.add(third)
        page = client_of(self.member).get(self.url("channel_detail")).content.decode()
        column = part(page, "forum-channels", "nav")
        self.assertIsNotNone(column)
        self.assertEqual(sorted(re.findall(r'href="(/forum/[^/"]+/)"', column)),
                         sorted([self.url("channel_detail"),
                                 self.url("channel_detail", community=third)]))
        self.assertNotIn(self.url("channel_detail", community=self.other), column)
        (current,) = [link for link in re.findall(r"<a\b[^>]*>", column)
                      if 'aria-current="page"' in link]
        self.assertIn(f'href="{self.url("channel_detail")}"', current)
        self.assertIn(f'href="{reverse("forum:channel_list")}"', column)
        # The same list as the forum's own page, in the same order.
        from toto.forum import access

        names = [community.name for community in access.communities_of(self.member)]
        places = [column.index(f">{name}<") for name in names]
        self.assertEqual(places, sorted(places))
        self.assertIn(">Mem<", part(page, "forum-viewer", "p"))

    def test_the_header_says_whose_channel_it_is_and_who_moderates(self):
        page = client_of(self.member).get(self.url("channel_detail")).content.decode()
        header = part(page, "forum-header", "header")
        self.assertIn("Guild", header)
        self.assertIn(str(self.guild.get_org_type_display()), header)
        self.assertIn("moderated by Head", header)
        self.assertIn(f'href="{reverse("socialhub:community_detail", args=[self.guild.slug])}"',
                      header)
        self.assertIn("data-forum-refresh", header)
        self.assertIn('data-forum-toggle="channels"', header)
        self.assertIn('data-forum-toggle="polls"', header)
        # A community without a head is moderated by the administrators.
        page = client_of(self.outsider).get(
            self.url("channel_detail", community=self.other)).content.decode()
        self.assertIn("moderated by the administrators", part(page, "forum-header", "header"))

    def test_a_heads_name_in_the_header_is_text(self):
        self.head_person.display_name = "<b>Head</b><script>x</script>"
        self.head_person.save()
        page = client_of(self.member).get(self.url("channel_detail")).content.decode()
        self.assertNotIn("<script>x</script>", page)
        self.assertIn("&lt;b&gt;Head&lt;/b&gt;", part(page, "forum-header", "header"))

    def test_the_settings_link_is_an_administrators(self):
        address = settings_url()
        for user, shown in ((self.admin, True), (self.head, False), (self.member, False)):
            page = client_of(user).get(self.url("channel_detail")).content.decode()
            header = part(page, "forum-header", "header")
            self.assertEqual('data-testid="forum-settings-link"' in header,
                             bool(shown and address), user.username)
            if shown and address:
                self.assertIn(f'href="{address}"', header)

    def test_the_page_is_the_windows_height_and_carries_the_scripts_words(self):
        page = client_of(self.member).get(self.url("channel_detail")).content.decode()
        self.assertNotIn("<footer", page)           # the composer is the page's last line
        box = re.search(r"<section\b[^>]*data-forum-channel[^>]*>", page, re.S).group(0)
        self.assertIn("100dvh", box)
        self.assertIn("group/forum", box)
        self.assertIn('data-forum-theme="light"', box)
        self.assertIn(":data-forum-theme=", box)    # the header's switch sets it
        words = re.search(r"<div\b[^>]*data-forum-words[^>]*>", page, re.S).group(0)
        for name in ("remove", "gone", "unreadable", "you", "vote", "close", "open", "closed",
                     "closes", "poll", "rule-open", "rule-final", "answers-count", "hidden-count",
                     "your-answer", "image", "image-type", "image-large", "live", "retrying",
                     "sending", "cost", "unaffordable", "failed", "confirm-remove"):
            self.assertRegex(words, rf'data-{name}="[^"]+"', name)
        self.assertIn('data-image-large="The image is too large. The most is 10 MB."', words)
        self.assertIn("{amount}", words)
        self.assertIn("{count}", words)
        self.assertIn("{when}", words)
        for hook in ("data-forum-scroll", "data-forum-messages", "data-forum-older",
                     "data-forum-new", "data-forum-empty", "data-forum-status", "data-forum-live",
                     "data-forum-post", "data-forum-text", "data-forum-image", "data-forum-pick",
                     "data-forum-picked", "data-forum-unpick", "data-forum-send",
                     "data-forum-estimate", "data-forum-polls", "data-forum-polls-empty",
                     "data-forum-poll-form", 'data-forum-panel="channels"',
                     'data-forum-panel="polls"'):
            self.assertIn(hook, page, hook)
        # A picture is chosen with the picture button: the file field itself
        # is hidden and takes the four raster types only.
        field = re.search(r"<input\b[^>]*data-forum-image[^>]*>", page).group(0)
        self.assertIn('accept="image/jpeg,image/png,image/gif,image/webp"', field)

    def test_the_theme_is_the_platforms_and_no_colour_is_the_pages_own(self):
        """Grounds, lines and accents are the theme's tokens, light and
        dark, as the platform's other pages name them (rounded cards on the
        bubble ground with the accent line); the page names no colour of its
        own and loads no stylesheet."""
        from django.template.loader import get_template

        card = ("darkMode ? 'border-accent-1 bg-bubble-bg-dark' : "
                "'border-accent-2 bg-bubble-bg-light'")
        for name in ("forum/channel.html", "forum/channel_list.html", "forum/settings.html",
                     "forum/refused.html"):
            source = open(get_template(name).origin.name, encoding="utf-8").read()
            self.assertNotRegex(source, r"#[0-9a-fA-F]{3,8}\b", name)
            self.assertNotRegex(source, r"\brgba?\(", name)
            self.assertNotIn("<style", source, name)
            self.assertNotIn("style=", source, name)
            self.assertNotIn("border-current/", source, name)       # which Tailwind 3 does not build
            self.assertNotIn("bg-current/", source, name)
            self.assertNotIn("ring-current/", source, name)
            self.assertNotIn("dark:", source, name)                 # the system's, not the switch's
            for colour in re.findall(r"(?:bg|text|border|divide)-(?:[a-z]+-)?(?:\d{2,3})\b", source):
                self.fail(f"{name} names a palette colour: {colour}")
            self.assertIn(card, source, name)
        channel = open(get_template("forum/channel.html").origin.name, encoding="utf-8").read()
        for variant in set(re.findall(r"group-data-\[[^\]]*\](?:/\w+)?:", channel)):
            self.assertEqual(variant, "group-data-[forum-theme=dark]/forum:")

    def test_the_script_writes_text_and_opens_no_socket(self):
        source = open(finders.find("forum/channel.js"), encoding="utf-8").read()
        code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        for word in ("innerHTML", "insertAdjacentHTML", "document.write", "WebSocket",
                     "EventSource", "setInterval", "eval("):
            self.assertNotIn(word, code, word)
        self.assertIn("textContent", code)


class FrameTests(ForumCase):
    def test_every_page_is_drawn_in_the_platforms_frame(self):
        """The list, a channel, the Settings page and a refusal are handed
        what every page of the platform is (``PageProcessor``): the app bar
        names the platform and carries the host's links, and the refusal
        keeps its status."""
        pages = ((self.admin, "/forum/", 200), (self.admin, self.url("channel_detail"), 200),
                 (self.admin, settings_url(), 200),
                 (self.outsider, self.url("channel_detail"), 403))
        for user, address, status in pages:
            response = client_of(user).get(address)
            self.assertEqual(response.status_code, status, address)
            page = response.content.decode()
            self.assertEqual(response.context["brand"]["name"], "T", address)
            self.assertEqual(response.context["platform"]["site_name"], "T", address)
            brand = re.search(r'<a\b[^>]*id="brand-link".*?</a>', page, re.S)
            self.assertIsNotNone(brand, address)
            self.assertRegex(brand.group(0), r"<h1\b[^>]*>\s*T\s*</h1>", address)
            for item in response.context["header_nav_items"]:
                self.assertIn(f'href="{item["url"]}"', page, address)


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
