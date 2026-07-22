from django.db.models import Q

from toto.core.connectors import (
    ReadOnlyModelConnector,
    filter_search,
    get_object_by_config,
    iso,
    minimal_person,
    register_connector,
)
from toto.locations.connectors import serialize_address


@register_connector
class PeopleReadConnector(ReadOnlyModelConnector):
    connector_type = "people_read"
    label = "People read"
    app_label = "people"

    def execute(self, input_data: dict | None = None) -> dict:
        from toto.people.models import Person

        input_data = input_data or {}
        action = self.config.get("action", "list")
        qs = Person.objects.select_related("address", "patron").prefetch_related("communities")

        if action == "get":
            person = get_object_by_config(qs, self, input_data, default_lookup="id")
            return {"data": {"person": self.serialize_person(person)}}

        if action == "search":
            query = self.query_value(input_data)
            qs = qs.filter(
                Q(display_name__icontains=query)
                | Q(slug__icontains=query)
                | Q(bio__icontains=query)
                | Q(email__icontains=query)
            )

        community_slug = self.configured_value("community_slug", "community_slug_field", input_data, default=None)
        if community_slug:
            qs = qs.filter(communities__slug=community_slug)

        return {
            "data": {
                "people": [
                    self.serialize_person(person)
                    for person in qs.distinct().order_by("display_name")[:self.limit()]
                ]
            }
        }

    def serialize_person(self, person) -> dict:
        data = {
            "id": person.id,
            "uid": str(person.uid),
            "display_name": person.display_name,
            "slug": person.slug,
            "bio": person.bio,
            "joined_date": iso(person.joined_date),
            "date_of_birth": iso(person.date_of_birth),
            "patron": minimal_person(person.patron) if person.patron else None,
            "address": serialize_address(person.address) if person.address else None,
            "communities": [
                {"id": community.id, "uid": str(community.uid), "name": community.name, "slug": community.slug}
                for community in person.communities.all()
            ],
        }
        if self.config.get("include_contact"):
            data["email"] = person.email
            data["phone"] = person.phone
        return data
