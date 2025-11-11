from finance.models import Account as SQLAccount, Transaction as SQLTransaction
from finance.graph.models import Account as GraphAccount, Transaction as GraphTransaction
from community.graph.models import CommunityMember as GraphMember
from toto.neo4j import ConversionStrategy


class AccountConversionStrategy(ConversionStrategy):
    def create(self, sql_obj):
        node = GraphAccount(
            uid=str(sql_obj.pk),
            name=sql_obj.name,
            balance=str(sql_obj.balance),
            currency_symbol=sql_obj.currency.symbol,
            currency_name=sql_obj.currency.name,
            active="true" if sql_obj.active else "false",
            metadata={"extra": getattr(sql_obj, "metadata", {})},
        ).save()

        # connect to owner if exists
        if sql_obj.owner_id:
            owner_node = GraphMember.nodes.get_or_none(uid=str(sql_obj.owner_id))
            if owner_node:
                node.owner.connect(owner_node)

        # connect to manager if exists
        if sql_obj.manager_id:
            manager_node = GraphMember.nodes.get_or_none(uid=str(sql_obj.manager_id))
            if manager_node:
                node.manager.connect(manager_node)

        return node

    def update(self, sql_obj, node):
        node.name = sql_obj.name
        node.balance = str(sql_obj.balance)
        node.currency_symbol = sql_obj.currency.symbol
        node.currency_name = sql_obj.currency.name
        node.active = "true" if sql_obj.active else "false"
        node.metadata = {"extra": getattr(sql_obj, "metadata", {})}
        node.save()
        return node

    def delete(self, sql_obj):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj):
        return GraphAccount.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphAccount.nodes.all()


class TransactionConversionStrategy(ConversionStrategy):
    def create(self, sql_obj):
        node = GraphTransaction(
            uid=str(sql_obj.pk),
            name=sql_obj.name,
            amount=str(sql_obj.amount),
            currency_symbol=sql_obj.currency.symbol,
            currency_name=sql_obj.currency.name,
            metadata={"extra": getattr(sql_obj, "metadata", {})},
        ).save()

        # connect to source account
        if sql_obj.source.id:
            src_node = GraphAccount.nodes.get_or_none(uid=str(sql_obj.source.id))
            if src_node:
                node.source_account.connect(src_node)

        # connect to destination account
        if sql_obj.destination.id:
            dst_node = GraphAccount.nodes.get_or_none(uid=str(sql_obj.destination.id))
            if dst_node:
                node.destination_account.connect(dst_node)

        return node

    def update(self, sql_obj, node):
        node.name = sql_obj.name
        node.amount = str(sql_obj.amount)
        node.currency_symbol = sql_obj.currency.symbol
        node.currency_name = sql_obj.currency.name
        node.metadata = {"extra": getattr(sql_obj, "metadata", {})}
        node.save()
        return node

    def delete(self, sql_obj):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj):
        return GraphTransaction.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphTransaction.nodes.all()
