from events.graph.models import Event as GraphEvent
from portfolio.graph.models import Company as GraphCompany
from community.graph.models import CommunityMember as GraphCommunityMember
from events.models import Event as SQLEvent
from portfolio.models import Company as SQLCompany
from community.models import CommunityMember as SQLCommunityMember
from toto.neo4j import ConversionStrategy


class EventConversionStrategy(ConversionStrategy):
    def create(self, sql_obj: SQLEvent):
        node = GraphEvent(
            uid=str(sql_obj.pk),
            title=sql_obj.title or "",
            description=sql_obj.description,
            location=sql_obj.location,
            start_time=sql_obj.start_time,
            end_time=sql_obj.end_time,
            public=sql_obj.public,
            event_type=sql_obj.category.name if sql_obj.category else None,
        ).save()

        company_node = GraphCompany.nodes.get_or_none(uid=str(sql_obj.company.pk))
        if company_node:
            node.company.connect(company_node)

        # connect to organizer (CommunityMember) if exists
        if sql_obj.organizer and hasattr(sql_obj.organizer, "community_profile"):
            cm_node = GraphCommunityMember.nodes.get_or_none(uid=str(sql_obj.organizer.community_profile.pk))
            if cm_node:
                node.organizer.connect(cm_node)

        return node

    def update(self, sql_obj: SQLEvent, node):
        node.title = sql_obj.title
        node.description = sql_obj.description
        node.location = sql_obj.location
        node.start_time = sql_obj.start_time
        node.end_time = sql_obj.end_time
        node.public = sql_obj.public
        node.event_type = sql_obj.category.name if sql_obj.category else None
        node.save()

        company_node = GraphCompany.nodes.get_or_none(uid=str(sql_obj.company.pk))
        if company_node and not node.company.is_connected(company_node):
            node.company.connect(company_node)

        # ensure organizer link
        if sql_obj.organizer and hasattr(sql_obj.organizer, "community_profile"):
            cm_node = GraphCommunityMember.nodes.get_or_none(uid=str(sql_obj.organizer.community_profile.pk))
            if cm_node and not node.organizer.is_connected(cm_node):
                node.organizer.connect(cm_node)

        return node

    def delete(self, sql_obj: SQLEvent):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj: SQLEvent):
        return GraphEvent.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphEvent.nodes.all()
