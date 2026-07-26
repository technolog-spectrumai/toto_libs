from django.db.models import Q

from toto.core.connectors import (
    ReadOnlyModelConnector,
    filter_search,
    get_object_by_config,
    iso,
    minimal_named,
    minimal_person,
    register_connector,
)
from toto.locations.connectors import serialize_address


@register_connector
class SocialhubReadConnector(ReadOnlyModelConnector):
    connector_type = "socialhub_read"
    label = "Socialhub read"
    app_label = "socialhub"
    allowed_resources = {"community", "news_post", "topic"}

    def validate(self) -> list[str]:
        errors = super().validate()
        resource = self.config.get("resource", "community")
        if resource not in self.allowed_resources:
            errors.append(
                f"Connector resource must be one of: {', '.join(sorted(self.allowed_resources))}."
            )
        return errors

    def execute(self, input_data: dict | None = None) -> dict:
        input_data = input_data or {}
        resource = self.config.get("resource", "community")

        if resource == "community":
            return self._execute_community(input_data)
        if resource == "news_post":
            return self._execute_news_post(input_data)
        if resource == "topic":
            return self._execute_topic(input_data)

        raise ValueError(f"Unsupported socialhub resource: {resource}")

    def _execute_community(self, input_data: dict) -> dict:
        from toto.socialhub.models import Community

        qs = Community.objects.select_related("location", "territory", "head")
        if self.config.get("action", "list") == "get":
            community = get_object_by_config(qs, self, input_data, default_lookup="slug")
            return {"data": {"community": serialize_community(community)}}
        query = self.query_value(input_data)
        qs = filter_search(qs, self, input_data, Q(name__icontains=query) | Q(slug__icontains=query))
        return {"data": {"communities": [serialize_community(item) for item in qs.order_by("name")[:self.limit()]]}}

    def _execute_news_post(self, input_data: dict) -> dict:
        from toto.socialhub.models import CommunityNewsPost

        qs = CommunityNewsPost.objects.select_related("community", "author").prefetch_related("topics")
        community_slug = self.configured_value("community_slug", "community_slug_field", input_data, default=None)
        if community_slug:
            qs = qs.filter(community__slug=community_slug)
        if self.config.get("action", "list") == "get":
            post = get_object_by_config(qs, self, input_data, default_lookup="id")
            return {"data": {"news_post": serialize_news_post(post)}}
        query = self.query_value(input_data)
        qs = filter_search(qs, self, input_data, Q(title__icontains=query) | Q(content__icontains=query))
        return {"data": {"news_posts": [serialize_news_post(item) for item in qs[:self.limit()]]}}

    def _execute_topic(self, input_data: dict) -> dict:
        from toto.socialhub.models import CommunityNewsTopic

        qs = CommunityNewsTopic.objects.all()
        if self.config.get("action", "list") == "get":
            topic = get_object_by_config(qs, self, input_data, default_lookup="slug")
            return {"data": {"topic": serialize_topic(topic)}}
        query = self.query_value(input_data)
        qs = filter_search(qs, self, input_data, Q(name__icontains=query) | Q(slug__icontains=query))
        return {"data": {"topics": [serialize_topic(item) for item in qs.order_by("name")[:self.limit()]]}}


def serialize_community(community) -> dict:
    return {
        "id": community.id,
        "uid": str(community.uid),
        "name": community.name,
        "slug": community.slug,
        "org_type": community.org_type,
        "established_year": community.established_year,
        "is_autonomous": community.is_autonomous,
        "is_foreign": community.is_foreign,
        "email": community.email,
        "head": minimal_person(community.head) if community.head else None,
        "location": serialize_address(community.location) if community.location else None,
        "territory": minimal_named(community.territory) if community.territory else None,
    }


def serialize_news_post(post) -> dict:
    return {
        "id": post.id,
        "uid": str(post.uid),
        "title": post.display_title,
        "excerpt": post.excerpt,
        "visibility": post.visibility,
        "created_at": iso(post.created_at),
        "updated_at": iso(post.updated_at),
        "community": minimal_named(post.community),
        "author": minimal_person(post.author) if post.author else None,
        "topics": [serialize_topic(topic) for topic in post.topics.all()],
    }


def serialize_topic(topic) -> dict:
    return {"id": topic.id, "uid": str(topic.uid), "name": topic.name, "slug": topic.slug}
