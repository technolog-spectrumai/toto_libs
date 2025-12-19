# ravioli/adapter/event_adapter.py
from ravioli.adapter import GraphAdapter
from events.models import Event  # adjust import to your app structure


class EventAdapter(GraphAdapter):
    """
    Converts Event instances into DataNodes and DataEdges
    using the generic GraphAdapter utilities.
    """

    EVENT_TYPE = "Event"
    CATEGORY_TYPE = "EventCategory"
    USER_TYPE = "User"

    REL_CATEGORY_TO_EVENT = "HAS_EVENT"
    REL_USER_TO_EVENT = "ORGANIZES"

    def add_event(self, event: Event):
        """
        Convert a single Event into graph nodes + edges.
        """

        # -----------------------------
        # 1. Event node
        # -----------------------------
        event_node = self.create_node(
            name=f"event:{event.id}",
            collection_type=self.EVENT_TYPE,
            data={
                "title": event.title,
                "description": event.description,
                "start_time": event.start_time.isoformat(),
                "end_time": event.end_time.isoformat(),
                "location": event.location,
                "public": event.public,
            }
        )

        # -----------------------------
        # 2. Category node + edge
        # -----------------------------
        if event.category:
            category_node = self.create_node(
                name=f"category:{event.category.id}",
                collection_type=self.CATEGORY_TYPE,
                data={
                    "name": event.category.name,
                    "description": event.category.description,
                }
            )
            self.create_edge(
                source=category_node,
                target=event_node,
                relation_type=self.REL_CATEGORY_TO_EVENT
            )

        # -----------------------------
        # 3. Organizer node + edge
        # -----------------------------
        if event.organizer:
            organizer_node = self.create_node(
                name=f"user:{event.organizer.id}",
                collection_type=self.USER_TYPE,
                data={
                    "username": event.organizer.username,
                    "email": event.organizer.email,
                }
            )
            self.create_edge(
                source=organizer_node,
                target=event_node,
                relation_type=self.REL_USER_TO_EVENT
            )

        return event_node

    # -----------------------------
    # Bulk conversion
    # -----------------------------
    def add_events(self, queryset):
        return [self.add_event(event) for event in queryset]
