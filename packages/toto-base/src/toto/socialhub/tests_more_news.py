"""Community news pages and the socialhub connector.

The news panel left the community page (``tests_forum_room``) but the posts,
their pages and the connector that reads them stayed. News is a community's;
a clearance (its own model since 2026-09-29) has none.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_more_news
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.connectors import ConnectorExecutionError, execute_connector_type
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community, CommunityNewsPost, CommunityNewsTopic

User = get_user_model()


class NewsCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.weavers = Community.objects.create(name="weavers", slug="weavers")
        cls.head_user = User.objects.create_user("head", password="pw")
        cls.head = Person.objects.create(user=cls.head_user, display_name="Head")
        cls.weavers.head = cls.head
        cls.weavers.save()
        cls.member_user = User.objects.create_user("member", password="pw")
        cls.member = Person.objects.create(user=cls.member_user, display_name="Member")
        cls.member.communities.add(cls.weavers)
        cls.root = User.objects.create_superuser("root", "root@example.com", "pw")
        cls.post = CommunityNewsPost.objects.create(community=cls.weavers, title="Loom day",
                                                    content="<p>Bring <b>wool</b>.</p>")


class NewsPostModelTests(TestCase):
    def test_the_text_is_read_without_its_markup(self):
        post = CommunityNewsPost(content="<p>Bring   <b>wool</b></p>\n<p>and tea.</p>")
        self.assertEqual(post.plain_text, "Bring wool and tea.")

    def test_a_post_without_a_title_is_named_by_its_start_and_never_blank(self):
        post = CommunityNewsPost(content="<p>" + "word " * 100 + "</p>")
        self.assertLessEqual(len(post.display_title), 220)
        self.assertTrue(post.display_title.startswith("word word"))
        self.assertEqual(CommunityNewsPost(content="").display_title, "Untitled post")

    def test_a_topic_gets_a_unique_slug(self):
        first = CommunityNewsTopic.objects.create(name="Wool")
        second = CommunityNewsTopic.objects.create(name="Wool!")
        self.assertEqual(first.slug, "wool")
        self.assertNotEqual(second.slug, first.slug)


class NewsPageTests(NewsCase):
    def test_the_head_publishes_and_lands_on_the_post(self):
        self.client.force_login(self.head_user)
        response = self.client.post(reverse("socialhub:community_news_create", args=["weavers"]), {
            "title": "Market", "content": "<p>Saturday</p>", "visibility": "public"})
        post = CommunityNewsPost.objects.get(title="Market")
        self.assertEqual(post.community, self.weavers)
        self.assertRedirects(response, post.get_absolute_url(), fetch_redirect_response=False)
        self.assertTrue(post.get_absolute_url().endswith(f"#community-news-post-{post.pk}"))

    def test_the_form_starts_with_the_author_filled_in(self):
        self.client.force_login(self.head_user)
        form = self.client.get(reverse("socialhub:community_news_create",
                                       args=["weavers"])).context["form"]
        self.assertEqual(form.initial["author"], self.head)

    def test_empty_content_is_refused(self):
        self.client.force_login(self.head_user)
        response = self.client.post(reverse("socialhub:community_news_create", args=["weavers"]),
                                    {"title": "Nothing", "content": "", "visibility": "public"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CommunityNewsPost.objects.filter(title="Nothing").exists())

    def test_a_plain_member_may_neither_publish_edit_nor_delete(self):
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.post(
            reverse("socialhub:community_news_create", args=["weavers"]),
            {"title": "x", "content": "<p>x</p>", "visibility": "public"}).status_code, 403)
        self.assertEqual(self.client.post(
            reverse("socialhub:community_news_update", args=[self.post.pk]),
            {"title": "hijacked", "content": "<p>x</p>", "visibility": "public"}).status_code, 403)
        self.assertEqual(self.client.post(
            reverse("socialhub:community_news_delete", args=[self.post.pk])).status_code, 403)
        self.post.refresh_from_db()
        self.assertEqual(self.post.title, "Loom day")

    def test_the_head_edits_then_deletes_after_a_confirmation_page(self):
        self.client.force_login(self.head_user)
        self.client.post(reverse("socialhub:community_news_update", args=[self.post.pk]),
                         {"title": "Loom night", "content": "<p>x</p>", "visibility": "community"})
        self.post.refresh_from_db()
        self.assertEqual((self.post.title, self.post.visibility), ("Loom night", "community"))

        confirm = self.client.get(reverse("socialhub:community_news_delete", args=[self.post.pk]))
        self.assertEqual(confirm.status_code, 200)
        self.assertTrue(CommunityNewsPost.objects.filter(pk=self.post.pk).exists())
        response = self.client.post(reverse("socialhub:community_news_delete", args=[self.post.pk]))
        self.assertRedirects(response, reverse("socialhub:community_detail", args=["weavers"]),
                             fetch_redirect_response=False)
        self.assertFalse(CommunityNewsPost.objects.filter(pk=self.post.pk).exists())

    def test_a_missing_community_is_a_404(self):
        self.client.force_login(self.root)
        self.assertEqual(self.client.get(reverse("socialhub:community_news_create",
                                                 args=["nowhere"])).status_code, 404)


class ConnectorTests(NewsCase):
    def run_connector(self, config, data=None):
        return execute_connector_type("socialhub_read", config, data or {})["data"]

    def test_a_post_nobody_wrote_is_a_connector_error(self):
        titles = [p["title"] for p in self.run_connector({"resource": "news_post"})["news_posts"]]
        self.assertEqual(titles, ["Loom day"])
        with self.assertRaises(ConnectorExecutionError):
            self.run_connector({"resource": "news_post", "action": "get",
                                "id": self.post.pk + 1000})

    def test_news_posts_narrow_to_one_community_and_serialise_the_post(self):
        other = Community.objects.create(name="spinners", slug="spinners")
        CommunityNewsPost.objects.create(community=other, title="Spin", content="<p>x</p>")
        rows = self.run_connector({"resource": "news_post", "community_slug": "weavers"})
        self.assertEqual([p["title"] for p in rows["news_posts"]], ["Loom day"])
        one = self.run_connector({"resource": "news_post", "action": "get",
                                  "id": self.post.pk})["news_post"]
        self.assertEqual(one["community"]["name"], "weavers")
        self.assertEqual(one["excerpt"], "Bring wool.")
        self.assertEqual(one["topics"], [])

    def test_topics_are_listed_and_fetched_by_slug(self):
        CommunityNewsTopic.objects.create(name="Wool")
        CommunityNewsTopic.objects.create(name="Dye")
        listed = self.run_connector({"resource": "topic"})["topics"]
        self.assertEqual([t["name"] for t in listed], ["Dye", "Wool"])
        self.assertEqual(self.run_connector({"resource": "topic", "action": "get",
                                             "slug": "wool"})["topic"]["name"], "Wool")
        searched = self.run_connector({"resource": "topic", "action": "search", "query": "dy"})
        self.assertEqual([t["name"] for t in searched["topics"]], ["Dye"])
        # A search with nothing to look for finds nothing, rather than everything.
        self.assertEqual(self.run_connector({"resource": "topic", "action": "search"})["topics"], [])

    def test_a_community_is_serialised_with_its_head(self):
        community = self.run_connector({"resource": "community", "action": "get",
                                        "slug": "weavers"})["community"]
        self.assertEqual(community["slug"], "weavers")
        self.assertEqual(community["head"]["display_name"], "Head")
        # The seat is text since 2026-10-04; no map address is serialised.
        self.assertIsNone(community["seat"])
        self.assertNotIn("location", community)

    def test_an_unknown_resource_is_a_validation_error(self):
        from toto.socialhub.connectors import SocialhubReadConnector

        errors = SocialhubReadConnector(config={"resource": "person"}).validate()
        self.assertTrue(any("must be one of" in error for error in errors))
