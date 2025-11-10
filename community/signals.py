from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
from community.models import Community, CommunityMember, Address
from community.graph import Community as GraphCommunity, CommunityMember as GraphMember, Address as GraphAddress


# -----------------------------
# Community sync
# -----------------------------
@receiver(post_save, sender=Community)
def sync_community_to_graph(sender, instance, created, **kwargs):
    if created:
        GraphCommunity(
            uid=str(instance.id),
            name=instance.name,
            slug=instance.slug,
            established_year=str(instance.established_year) if instance.established_year else None
        ).save()
    else:
        try:
            g = GraphCommunity.nodes.get(uid=str(instance.id))
            g.name = instance.name
            g.slug = instance.slug
            g.established_year = str(instance.established_year) if instance.established_year else None
            g.save()
        except GraphCommunity.DoesNotExist:
            pass


@receiver(post_delete, sender=Community)
def delete_community_from_graph(sender, instance, **kwargs):
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
    if created:
        GraphMember(
            uid=str(instance.id),
            display_name=instance.display_name,
            slug=instance.slug,
            bio=instance.bio
        ).save()
    else:
        try:
            g = GraphMember.nodes.get(uid=str(instance.id))
            g.display_name = instance.display_name
            g.slug = instance.slug
            g.bio = instance.bio
            g.save()
        except GraphMember.DoesNotExist:
            pass


@receiver(post_delete, sender=CommunityMember)
def delete_member_from_graph(sender, instance, **kwargs):
    try:
        g = GraphMember.nodes.get(uid=str(instance.id))
        g.delete()
    except GraphMember.DoesNotExist:
        pass


# -----------------------------
# Address sync
# -----------------------------
@receiver(post_save, sender=Address)
def sync_address_to_graph(sender, instance, created, **kwargs):
    if created:
        GraphAddress(
            uid=str(instance.id),
            country_name=instance.country_name,
            state_or_province_name=instance.state_or_province_name,
            locality_name=instance.locality_name,
            street=instance.street,
            building=instance.building,
            apartment=instance.apartment
        ).save()
    else:
        try:
            g = GraphAddress.nodes.get(uid=str(instance.id))
            g.country_name = instance.country_name
            g.state_or_province_name = instance.state_or_province_name
            g.locality_name = instance.locality_name
            g.street = instance.street
            g.building = instance.building
            g.apartment = instance.apartment
            g.save()
        except GraphAddress.DoesNotExist:
            pass


@receiver(post_delete, sender=Address)
def delete_address_from_graph(sender, instance, **kwargs):
    try:
        g = GraphAddress.nodes.get(uid=str(instance.id))
        g.delete()
    except GraphAddress.DoesNotExist:
        pass


# -----------------------------
# Relationship sync (CommunityMember ↔ Community)
# -----------------------------
@receiver(m2m_changed, sender=CommunityMember.communities.through)
def sync_membership_relation(sender, instance, action, reverse, model, pk_set, **kwargs):
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
