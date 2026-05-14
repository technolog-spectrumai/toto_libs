from toto.noosphere.adapters import BaseSyncAdapter
from toto.noosphere.registry import register_sync_adapter

from .models import Community, Person


@register_sync_adapter
class CommunitySyncAdapter(BaseSyncAdapter):
    model = Community
    allowed_fields = [
        "name",
        "slug",
        "org_type",
        "established_year",
        "is_autonomous",
        "email",
        "is_foreign",
    ]


@register_sync_adapter
class PersonSyncAdapter(BaseSyncAdapter):
    model = Person
    allowed_fields = [
        "display_name",
        "bio",
        "slug",
        "joined_date",
        "date_of_birth",
        "email",
        "phone",
    ]
