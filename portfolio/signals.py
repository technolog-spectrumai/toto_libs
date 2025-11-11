# signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Company as SQLCompany, Shareholder as SQLShareholder, FundingRound as SQLFundingRound
from .graph import Company as GraphCompany, Shareholder as GraphShareholder, FundingRound as GraphFundingRound
from oya.neo4j import is_neo4j_connected

# -----------------------------
# Company Sync
# -----------------------------
@receiver(post_save, sender=SQLCompany)
def sync_company_to_graph(sender, instance, created, **kwargs):
    """
    Sync SQL Company -> Neo4j Company node
    """
    if not is_neo4j_connected():
        return
    node = GraphCompany.nodes.get_or_none(uid=str(instance.pk))
    if not node:
        node = GraphCompany(
            uid=str(instance.pk),
            social_id=str(instance.id),  # SocialEntity.id
            name=instance.name,
            registration_number=instance.registration_number,
            country=instance.country,
            industry=instance.industry,
            date_founded=instance.date_founded,
            is_active=instance.is_active,
            metadata={}
        )
        node.save()
    else:
        node.name = instance.name
        node.registration_number = instance.registration_number
        node.country = instance.country
        node.industry = instance.industry
        node.date_founded = instance.date_founded
        node.is_active = instance.is_active
        node.save()


@receiver(post_delete, sender=SQLCompany)
def delete_company_from_graph(sender, instance, **kwargs):
    if not is_neo4j_connected():
        return
    node = GraphCompany.nodes.get_or_none(uid=str(instance.pk))
    if node:
        node.delete()


# -----------------------------
# Shareholder Sync
# -----------------------------
@receiver(post_save, sender=SQLShareholder)
def sync_shareholder_to_graph(sender, instance, created, **kwargs):
    """
    Sync SQL Shareholder -> Neo4j Shareholder node + relationship to Company
    """
    if not is_neo4j_connected():
        return
    node = GraphShareholder.nodes.get_or_none(uid=str(instance.pk))
    if not node:
        node = GraphShareholder(
            uid=str(instance.pk),
            social_id=str(instance.social_entity_id),
            is_active=instance.is_active,
            metadata={"full_name": instance.get_full_name(), "email": instance.get_email()}
        )
        node.save()
    else:
        node.is_active = instance.is_active
        node.metadata.update({"full_name": instance.get_full_name(), "email": instance.get_email()})
        node.save()

    # Ensure relationship to Company exists (avoid duplicates)
    company_node = GraphCompany.nodes.get_or_none(uid=str(instance.company.pk))
    if company_node:
        # Disconnect old relationship if exists
        node.company.disconnect(company_node)
        # Connect fresh relationship with updated properties
        node.company.connect(company_node, {
            "shares_owned": int(instance.shares_owned),
            "date_joined": instance.date_joined,
            "is_active": instance.is_active,
            "metadata": {}
        })


@receiver(post_delete, sender=SQLShareholder)
def delete_shareholder_from_graph(sender, instance, **kwargs):
    if not is_neo4j_connected():
        return
    node = GraphShareholder.nodes.get_or_none(uid=str(instance.pk))
    if node:
        node.delete()


# -----------------------------
# FundingRound Sync
# -----------------------------
@receiver(post_save, sender=SQLFundingRound)
def sync_fundinground_to_graph(sender, instance, created, **kwargs):
    """
    Sync SQL FundingRound -> Neo4j FundingRound node + relationship to Company
    """
    if not is_neo4j_connected():
        return
    node = GraphFundingRound.nodes.get_or_none(uid=str(instance.pk))
    if not node:
        node = GraphFundingRound(
            uid=str(instance.pk),
            name=instance.name,
            amount=float(instance.amount),   # ✅ cast DecimalField to float
            currency=instance.currency.symbol,
            timestamp=instance.timestamp,
            metadata={}
        )
        node.save()
    else:
        node.name = instance.name
        node.amount = float(instance.amount)
        node.currency = instance.currency.symbol
        node.timestamp = instance.timestamp
        node.save()

    # Ensure relationship to Company exists (avoid duplicates)
    company_node = GraphCompany.nodes.get_or_none(uid=str(instance.venture.pk))
    if company_node:
        company_node.funding_rounds.disconnect(node)
        company_node.funding_rounds.connect(node, {
            "amount": float(instance.amount),
            "currency": instance.currency.symbol,
            "metadata": {}
        })


@receiver(post_delete, sender=SQLFundingRound)
def delete_fundinground_from_graph(sender, instance, **kwargs):
    if not is_neo4j_connected():
        return
    node = GraphFundingRound.nodes.get_or_none(uid=str(instance.pk))
    if node:
        node.delete()
