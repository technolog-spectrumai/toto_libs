from federal.models import Federation as SQLFederation, FederatedIdentity as SQLIdentity
from federal.graph.models import Federation as GraphFederation, FederatedIdentity as GraphIdentity
from toto.neo4j import ConversionStrategy


class FederationConversionStrategy(ConversionStrategy):
    def create(self, sql_obj):
        return GraphFederation(
            uid=str(sql_obj.pk),
            name=sql_obj.name,
            slug=sql_obj.slug,
            description=sql_obj.description,
            active=sql_obj.active,
            metadata={"platform": sql_obj.platform_id},
        ).save()

    def update(self, sql_obj, node):
        node.name = sql_obj.name
        node.slug = sql_obj.slug
        node.description = sql_obj.description
        node.active = sql_obj.active
        node.metadata = {"platform": sql_obj.platform_id}
        node.save()
        return node

    def delete(self, sql_obj):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj):
        return GraphFederation.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphFederation.nodes.all()


class IdentityConversionStrategy(ConversionStrategy):
    def create(self, sql_obj):
        node = GraphIdentity(
            uid=str(sql_obj.pk),
            name=sql_obj.name,
            issuer=sql_obj.issuer,
            metadata={"rsa_keypair": sql_obj.rsa_keypair_id},
        ).save()
        # connect to federation
        federation_node = GraphFederation.nodes.get_or_none(uid=str(sql_obj.federation.pk))
        if federation_node:
            node.federation.connect(federation_node)
            federation_node.identities.connect(node)
        return node

    def update(self, sql_obj, node):
        node.name = sql_obj.name
        node.issuer = sql_obj.issuer
        node.metadata = {"rsa_keypair": sql_obj.rsa_keypair_id}
        node.save()
        return node

    def delete(self, sql_obj):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj):
        return GraphIdentity.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphIdentity.nodes.all()
