from community.models import Community as SQLCommunity, CommunityMember as SQLCommunityMember
from community.graph.models import Community as GraphCommunity, CommunityMember as GraphMember
from federal.graph.models import Federation as GraphFederation, FederatedIdentity as GraphIdentity
from toto.neo4j import ConversionStrategy
from federal.models import FederatedIdentity as SQLIdentity


class CommunityConversionStrategy(ConversionStrategy):
    def create(self, sql_obj):
        node = GraphCommunity(
            uid=str(sql_obj.id),
            name=sql_obj.name,
            established_year=str(sql_obj.established_year) if sql_obj.established_year else None,
        ).save()

        if sql_obj.federation_id:
            fed_node = GraphFederation.nodes.get_or_none(uid=str(sql_obj.federation_id))
            if fed_node:
                node.federation.connect(fed_node, {"role": "affiliate"})
        return node

    def update(self, sql_obj, node):
        node.name = sql_obj.name
        node.established_year = str(sql_obj.established_year) if sql_obj.established_year else None
        node.save()
        return node

    def delete(self, sql_obj):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj):
        return GraphCommunity.nodes.get_or_none(uid=str(sql_obj.id))

    def get_all_nodes(self):
        return GraphCommunity.nodes.all()


class CommunityMemberConversionStrategy(ConversionStrategy):
    def create(self, sql_obj):
        node = GraphMember(
            uid=str(sql_obj.id),
            display_name=sql_obj.display_name,
            bio=sql_obj.bio,
            user_id=str(sql_obj.user_id) if sql_obj.user_id else None,
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

        # connect to federated identity via user_id
        if sql_obj.user_id:
            fed_identity = SQLIdentity.objects.filter(user_id=sql_obj.user_id).first()
            if fed_identity:
                identity_node = GraphIdentity.nodes.get_or_none(uid=str(fed_identity.pk))
                if identity_node:
                    node.identity.connect(identity_node)
        return node

    def update(self, sql_obj, node):
        node.display_name = sql_obj.display_name
        node.bio = sql_obj.bio
        node.user_id = str(sql_obj.user_id) if sql_obj.user_id else None
        node.save()

        # ensure federated identity link stays updated
        if sql_obj.user_id:
            fed_identity = SQLIdentity.objects.filter(user_id=sql_obj.user_id).first()
            if fed_identity:
                identity_node = GraphIdentity.nodes.get_or_none(uid=str(fed_identity.pk))
                if identity_node and not node.identity.is_connected(identity_node):
                    node.identity.connect(identity_node)

        return node

    def delete(self, sql_obj):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj):
        return GraphMember.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphMember.nodes.all()



