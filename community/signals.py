from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
from community.models import Community, CommunityMember
from community.graph import Community as GraphCommunity, CommunityMember as GraphMember
from toto.neo4j import is_connected as is_neo4j_connected
from federal.graph import Federation as GraphFederation
from federal.models import Federation

# -----------------------------
# Community sync
# -----------------------------
@receiver(post_save, sender=Community)
def sync_community_to_graph(sender, instance, created, **kwargs):
    if not is_neo4j_connected():
        return
    social_id = str(instance.id)  # Community is itself a SocialEntity
    if created:
        g = GraphCommunity(
            uid=str(instance.id),
            social_id=social_id,
            name=instance.name,
            slug=instance.slug,
            established_year=str(instance.established_year) if instance.established_year else None
        ).save()
    else:
        try:
            g = GraphCommunity.nodes.get(uid=str(instance.id))
            g.social_id = social_id
            g.name = instance.name
            g.slug = instance.slug
            g.established_year = str(instance.established_year) if instance.established_year else None
            g.save()
        except GraphCommunity.DoesNotExist:
            pass
    if instance.federation:
        try:
            g_fed = GraphFederation.nodes.get(uid=str(instance.federation.id))
            g.federation.connect(g_fed, {"role": "affiliate"})
        except GraphFederation.DoesNotExist:
            pass


@receiver(post_delete, sender=Community)
def delete_community_from_graph(sender, instance, **kwargs):
    if not is_neo4j_connected():
        return
    try:
        g = GraphCommunity.nodes.get(uid=str(instance.id))
        g.delete()
    except GraphCommunity.DoesNotExist:
        pass


# -----------------------------
# CommunityMember sync
# -----------------------------
@receiver(post_save, sender=CommunityMember)
def sync_member_to_graph(sender, instance, created, **kwargs):
    if not is_neo4j_connected():
        return
    social_id = str(instance.id)  # CommunityMember is itself a SocialEntity
    if created:
        GraphMember(
            uid=str(instance.id),
            social_id=social_id,
            display_name=instance.display_name,
            slug=instance.slug,
            bio=instance.bio
        ).save()
    else:
        try:
            g = GraphMember.nodes.get(uid=str(instance.id))
            g.social_id = social_id
            g.display_name = instance.display_name
            g.slug = instance.slug
            g.bio = instance.bio
            g.save()
        except GraphMember.DoesNotExist:
            pass


@receiver(post_delete, sender=CommunityMember)
def delete_member_from_graph(sender, instance, **kwargs):
    if not is_neo4j_connected():
        return
    try:
        g = GraphMember.nodes.get(uid=str(instance.id))
        g.delete()
    except GraphMember.DoesNotExist:
        pass