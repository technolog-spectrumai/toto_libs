from toto.noosphere.adapters import BaseSyncAdapter
from toto.noosphere.registry import register_sync_adapter

from .models import EventCategory, ScheduledEvent


@register_sync_adapter
class EventCategorySyncAdapter(BaseSyncAdapter):
    model = EventCategory
    allowed_fields = [
        "name",
        "description",
    ]


@register_sync_adapter
class ScheduledEventSyncAdapter(BaseSyncAdapter):
    model = ScheduledEvent
    allowed_fields = [
        "title",
        "description",
        "start_time",
        "end_time",
        "public",
        "requires_registration",
        "capacity",
    ]
