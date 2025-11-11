from community.models import Community as SQLCommunity, CommunityMember as SQLCommunityMember
from community.graph.models import Community as GraphCommunity, CommunityMember as GraphMember
from federal.graph.models import Federation as GraphFederation
from toto.neo4j import ConversionStrategy


class CommunityConversionStrategy(ConversionStrategy):
    def create(self, sql_obj):
        node = GraphCommunity(
            uid=str(sql_obj.pk),
            social_id=str(sql_obj.id),
            name=sql_obj.name,
            established_year=str(sql_obj.established_year) if sql_obj.established_year else None,
        ).save()

        # connect to federation if exists
        if sql_obj.federation_id:
            fed_node = GraphFederation.nodes.get_or_none(uid=str(sql_obj.federation_id))
            if fed_node:
                node.federation.connect(fed_node, {"role": "affiliate"})
        return node

    def update(self, sql_obj, node):
        node.social_id = str(sql_obj.id)
        node.name = sql_obj.name
        node.established_year = str(sql_obj.established_year) if sql_obj.established_year else None
        node.save()
        return node

    def delete(self, sql_obj):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj):
        return GraphCommunity.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphCommunity.nodes.all()


class CommunityMemberConversionStrategy(ConversionStrategy):
    def create(self, sql_obj):
        node = GraphMember(
            uid=str(sql_obj.pk),
            social_id=str(sql_obj.id),
            display_name=sql_obj.display_name,
            bio=sql_obj.bio,
        ).save()

        # connect to communities
        for community in sql_obj.communities.all():
            c_node = GraphCommunity.nodes.get_or_none(uid=str(community.pk))
            if c_node:
                node.communities.connect(c_node, {"role": "member"})

        # connect to patron if exists
        if sql_obj.patron_id:
            patron_node = GraphMember.nodes.get_or_none(uid=str(sql_obj.patron_id))
            if patron_node:
                node.patron.connect(patron_node)

        return node

    def update(self, sql_obj, node):
        node.social_id = str(sql_obj.id)
        node.display_name = sql_obj.display_name
        node.bio = sql_obj.bio
        node.save()
        return node

    def delete(self, sql_obj):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj):
        return GraphMember.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphMember.nodes.all()
