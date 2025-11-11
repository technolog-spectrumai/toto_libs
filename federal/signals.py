# signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Federation as SQLFederation, FederatedIdentity as SQLIdentity
from .graph import Federation as GraphFederation, FederatedIdentity as GraphIdentity
from toto.neo4j import is_connected as is_neo4j_connected
# -----------------------------
# Federation Sync
# -----------------------------
@receiver(post_save, sender=SQLFederation)
def sync_federation_to_graph(sender, instance, created, **kwargs):
    """
    Sync SQL Federation -> Neo4j Federation node
    """
    if not is_neo4j_connected():
        return

    node = GraphFederation.nodes.get_or_none(uid=str(instance.pk))
    if not node:
        node = GraphFederation(
            uid=str(instance.pk),
            name=instance.name,
            slug=instance.slug,
            description=instance.description,
            active=instance.active,
            metadata={}
        )
        node.save()
    else:
        node.name = instance.name
        node.slug = instance.slug
        node.description = instance.description
        node.active = instance.active
        node.save()


@receiver(post_delete, sender=SQLFederation)
def delete_federation_from_graph(sender, instance, **kwargs):
    if not is_neo4j_connected():
        return

    node = GraphFederation.nodes.get_or_none(uid=str(instance.pk))
    if node:
        node.delete()


# -----------------------------
# FederatedIdentity Sync
# -----------------------------
@receiver(post_save, sender=SQLIdentity)
def sync_identity_to_graph(sender, instance, created, **kwargs):
    """
    Sync SQL FederatedIdentity -> Neo4j FederatedIdentity node + relationships to Federation
    """
    if not is_neo4j_connected():
        return

    node = GraphIdentity.nodes.get_or_none(uid=str(instance.pk))
    if not node:
        node = GraphIdentity(
            uid=str(instance.pk),
            name=instance.name,
            issuer=instance.issuer,
            metadata={}
        )
        node.save()
    else:
        node.name = instance.name
        node.issuer = instance.issuer
        node.save()

    # Ensure relationships to Federation exist (avoid duplicates)
    federation_node = GraphFederation.nodes.get_or_none(uid=str(instance.federation.pk))
    if federation_node:
        # Identity BELONGS_TO Federation
        node.federation.disconnect(federation_node)
        node.federation.connect(federation_node, {"metadata": {}})

        # Federation RECOGNIZES Identity
        federation_node.identities.disconnect(node)
        federation_node.identities.connect(node, {"metadata": {}})


@receiver(post_delete, sender=SQLIdentity)
def delete_identity_from_graph(sender, instance, **kwargs):
    if not is_neo4j_connected():
        return

    node = GraphIdentity.nodes.get_or_none(uid=str(instance.pk))
    if node:
        node.delete()
