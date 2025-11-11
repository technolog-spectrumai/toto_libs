from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Account, Transaction   # Django ORM models
from .graph import Account as GraphAccount, Transaction as GraphTransaction
from community.graph import CommunityMember as GraphMember  # Neo4j CommunityMember node
from toto.neo4j import is_connected as is_neo4j_connected
# -----------------------------
# Account Sync
# -----------------------------

@receiver(post_save, sender=Account)
def sync_account_to_graph(sender, instance, created, **kwargs):
    if not is_neo4j_connected():
        return

    try:
        g_acc = GraphAccount.nodes.get(uid=str(instance.id))
    except GraphAccount.DoesNotExist:
        g_acc = GraphAccount(uid=str(instance.id))

    # Update properties
    g_acc.name = instance.name
    g_acc.balance = str(instance.balance)
    g_acc.currency_symbol = instance.currency.symbol
    g_acc.currency_name = instance.currency.name
    g_acc.active = str(instance.active)
    g_acc.metadata = {}
    g_acc.save()

    # Owner relationship (SocialEntity → CommunityMember/Company)
    if instance.owner:
        try:
            g_owner = GraphMember.nodes.get(social_id=str(instance.owner.id))
            g_acc.owner.disconnect_all()
            g_acc.owner.connect(g_owner)
        except GraphMember.DoesNotExist:
            pass

    # Manager relationship (User → CommunityMember)
    if instance.manager and hasattr(instance.manager, "community_profile"):
        try:
            g_manager = GraphMember.nodes.get(uid=str(instance.manager.community_profile.id))
            g_acc.manager.disconnect_all()
            g_acc.manager.connect(g_manager)
        except GraphMember.DoesNotExist:
            pass


@receiver(post_delete, sender=Account)
def delete_account_from_graph(sender, instance, **kwargs):
    if not is_neo4j_connected():
        return
    try:
        g_acc = GraphAccount.nodes.get(uid=str(instance.id))
        g_acc.delete()
    except GraphAccount.DoesNotExist:
        pass


# -----------------------------
# Transaction Sync
# -----------------------------

@receiver(post_save, sender=Transaction)
def sync_transaction_to_graph(sender, instance, created, **kwargs):
    if not is_neo4j_connected():
        return

    try:
        g_txn = GraphTransaction.nodes.get(uid=str(instance.id))
    except GraphTransaction.DoesNotExist:
        g_txn = GraphTransaction(uid=str(instance.id))

    # Update properties
    g_txn.name = instance.name
    g_txn.amount = str(instance.amount)
    g_txn.currency_symbol = instance.currency.symbol
    g_txn.currency_name = instance.currency.name
    g_txn.timestamp = instance.timestamp
    g_txn.metadata = {}
    g_txn.save()

    # Connect to source account
    try:
        g_src = GraphAccount.nodes.get(uid=str(instance.source.id))
        g_txn.source_account.disconnect_all()
        g_txn.source_account.connect(g_src)
    except GraphAccount.DoesNotExist:
        pass

    # Connect to destination account
    try:
        g_dst = GraphAccount.nodes.get(uid=str(instance.destination.id))
        g_txn.destination_account.disconnect_all()
        g_txn.destination_account.connect(g_dst)
    except GraphAccount.DoesNotExist:
        pass


@receiver(post_delete, sender=Transaction)
def delete_transaction_from_graph(sender, instance, **kwargs):
    if not is_neo4j_connected():
        return
    try:
        g_txn = GraphTransaction.nodes.get(uid=str(instance.id))
        g_txn.delete()
    except GraphTransaction.DoesNotExist:
        pass
