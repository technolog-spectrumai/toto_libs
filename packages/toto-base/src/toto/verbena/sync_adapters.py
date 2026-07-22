from toto.noosphere.adapters import BaseSyncAdapter
from toto.noosphere.registry import register_sync_adapter

from .models import Tag, Page, Section


@register_sync_adapter
class TagSyncAdapter(BaseSyncAdapter):
    model = Tag
    allowed_fields = [
        "name",
        "slug",
    ]


@register_sync_adapter
class PageSyncAdapter(BaseSyncAdapter):
    model = Page
    allowed_fields = [
        "title",
        "slug",
        "description",
    ]


@register_sync_adapter
class SectionSyncAdapter(BaseSyncAdapter):
    model = Section
    allowed_fields = [
        "title",
        "content",
        "order",
    ]
