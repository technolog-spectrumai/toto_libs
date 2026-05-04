from toto.events.models import Event, EventCategory
from toto.socialhub.models import CommunityMember
from toto.events.graph.models import EventNode, EventCategoryNode
from toto.socialhub.graph.models import CommunityMember as MemberNode


# ---------------------------------------------------------
# CATEGORY PROJECTION
# ---------------------------------------------------------

class EventCategoryProjection:
    model = "EventCategory"
    app = "events"

    def sync_nodes(self):
        for c in EventCategory.objects.all():
            node = EventCategoryNode.nodes.get_or_none(uuid=str(c.id))

            if not node:
                node = EventCategoryNode(
                    uuid=str(c.id),
                    name=c.name,
                )
            else:
                node.name = c.name

            node.save()

    def sync_edges(self):
        pass  # Categories only connect from Event side


# ---------------------------------------------------------
# EVENT PROJECTION
# ---------------------------------------------------------

class EventProjection:
    model = "Event"
    app = "events"

    def sync_nodes(self):
        for e in Event.objects.all():
            node = EventNode.nodes.get_or_none(uuid=str(e.id))

            if not node:
                node = EventNode(
                    uuid=str(e.id),
                    title=e.title,
                    location=e.location,
                    start_time=e.start_time,
                    end_time=e.end_time,
                    public=e.public,
                )
            else:
                node.title = e.title
                node.location = e.location
                node.start_time = e.start_time
                node.end_time = e.end_time
                node.public = e.public

            node.save()

    def sync_edges(self):
        for e in Event.objects.all():
            ge = EventNode.nodes.get(uuid=str(e.id))

            ge.organizer.disconnect_all()
            ge.category.disconnect_all()

            # Organizer
            if e.organizer_id:
                gm = MemberNode.nodes.get_or_none(uuid=str(e.organizer.uid))
                if gm:
                    ge.organizer.connect(gm)

            # Category
            if e.category_id:
                cat = EventCategoryNode.nodes.get_or_none(uuid=str(e.category.id))
                if cat:
                    ge.category.connect(cat)
