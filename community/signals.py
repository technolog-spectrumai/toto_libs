from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
from community.models import Community, CommunityMember
from community.graph import Community as GraphCommunity, CommunityMember as GraphMember
from oya.check_neo4j import is_neo4j_connected

# -----------------------------
# Community sync
# -----------------------------
@receiver(post_save, sender=Community)
def sync_community_to_graph(sender, instance, created, **kwargs):
    if not is_neo4j_connected():
        return
    social_id = str(instance.social_entity.id) if instance.social_entity else None
    if created:
        GraphCommunity(
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
    social_id = str(instance.social_entity.id) if instance.social_entity else None
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


# -----------------------------
# Relationship sync (CommunityMember ↔ Community)
# -----------------------------
@receiver(m2m_changed, sender=CommunityMember.communities.through)
def sync_membership_relation(sender, instance, action, reverse, model, pk_set, **kwargs):
    if not is_neo4j_connected():
        return
    """
    Sync MEMBER_OF relationships when CommunityMember.communities changes.
    """
    if action == "post_add":
        for pk in pk_set:
            try:
                community = Community.objects.get(pk=pk)
                g_member = GraphMember.nodes.get(uid=str(instance.id))
                g_community = GraphCommunity.nodes.get(uid=str(community.id))
                g_member.communities.connect(g_community, {"role": "member"})
            except (Community.DoesNotExist, GraphMember.DoesNotExist, GraphCommunity.DoesNotExist):
                pass

    elif action == "post_remove":
        for pk in pk_set:
            try:
                community = Community.objects.get(pk=pk)
                g_member = GraphMember.nodes.get(uid=str(instance.id))
                g_community = GraphCommunity.nodes.get(uid=str(community.id))
                g_member.communities.disconnect(g_community)
            except (Community.DoesNotExist, GraphMember.DoesNotExist, GraphCommunity.DoesNotExist):
                pass
