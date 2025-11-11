from finance.models import Transaction as SQLTransaction
from portfolio.models import Company as SQLCompany, Shareholder as SQLShareholder, FundingRound as SQLFundingRound
from portfolio.graph.models import Company as GraphCompany, Shareholder as GraphShareholder, FundingRound as GraphFundingRound
from finance.graph.models import Transaction as GraphTransaction
from toto.neo4j import ConversionStrategy


class CompanyConversionStrategy(ConversionStrategy):
    def create(self, sql_obj):
        node = GraphCompany(
            uid=str(sql_obj.pk),
            social_id=str(sql_obj.id),
            name=sql_obj.name,
            registration_number=sql_obj.registration_number,
            country=sql_obj.country,
            industry=sql_obj.industry,
            date_founded=sql_obj.date_founded,
            is_active=sql_obj.is_active,
        ).save()
        return node

    def update(self, sql_obj, node):
        node.social_id = str(sql_obj.id)
        node.name = sql_obj.name
        node.registration_number = sql_obj.registration_number
        node.country = sql_obj.country
        node.industry = sql_obj.industry
        node.date_founded = sql_obj.date_founded
        node.is_active = sql_obj.is_active
        node.save()
        return node

    def delete(self, sql_obj):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj):
        return GraphCompany.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphCompany.nodes.all()


class ShareholderConversionStrategy(ConversionStrategy):
    def create(self, sql_obj):
        node = GraphShareholder(
            uid=str(sql_obj.pk),
            social_id=str(sql_obj.social_entity.id),
            is_active=sql_obj.is_active,
            metadata={
                "full_name": sql_obj.get_full_name()
            },
        ).save()

        # connect to company
        company_node = GraphCompany.nodes.get_or_none(uid=str(sql_obj.company.pk))
        if company_node:
            node.company.connect(company_node, {
                "shares_owned": sql_obj.shares_owned,
                "date_joined": sql_obj.date_joined,
                "is_active": sql_obj.is_active,
            })
        return node

    def update(self, sql_obj, node):
        node.social_id = str(sql_obj.social_entity.id)
        node.is_active = sql_obj.is_active
        node.metadata = {
            "full_name": sql_obj.get_full_name()
        }
        node.save()

        # update relationship
        company_node = GraphCompany.nodes.get_or_none(uid=str(sql_obj.company.pk))
        if company_node and not node.company.is_connected(company_node):
            node.company.connect(company_node, {
                "shares_owned": sql_obj.shares_owned,
                "date_joined": sql_obj.date_joined,
                "is_active": sql_obj.is_active,
            })
        return node

    def delete(self, sql_obj):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj):
        return GraphShareholder.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphShareholder.nodes.all()


class FundingRoundConversionStrategy(ConversionStrategy):
    def create(self, sql_obj):
        node = GraphFundingRound(
            uid=str(sql_obj.pk),
            name=sql_obj.name,
            amount=float(sql_obj.amount),
            currency=sql_obj.currency.symbol,
            timestamp=sql_obj.timestamp,
        ).save()

        # connect to venture company
        company_node = GraphCompany.nodes.get_or_none(uid=str(sql_obj.venture.pk))
        if company_node:
            node.venture.connect(company_node, {
                "amount": float(sql_obj.amount),
                "currency": sql_obj.currency.symbol,
            })

        # connect to transaction if exists
        if sql_obj.transaction:
            tx_node = GraphTransaction.nodes.get_or_none(uid=str(sql_obj.transaction.pk))
            if not tx_node:
                tx_node = GraphTransaction(
                    uid=str(sql_obj.transaction.pk),
                    amount=float(sql_obj.transaction.amount),
                    currency=sql_obj.transaction.currency.symbol,
                    timestamp=sql_obj.transaction.timestamp,
                    metadata={"source": "finance"},
                ).save()
            node.transaction.connect(tx_node)

        return node

    def update(self, sql_obj, node):
        node.name = sql_obj.name
        node.amount = float(sql_obj.amount)
        node.currency = sql_obj.currency.symbol
        node.timestamp = sql_obj.timestamp
        node.save()

        # ensure venture link
        company_node = GraphCompany.nodes.get_or_none(uid=str(sql_obj.venture.pk))
        if company_node and not node.venture.is_connected(company_node):
            node.venture.connect(company_node, {
                "amount": float(sql_obj.amount),
                "currency": sql_obj.currency.symbol,
            })

        # ensure transaction link
        if sql_obj.transaction:
            tx_node = GraphTransaction.nodes.get_or_none(uid=str(sql_obj.transaction.pk))
            if not tx_node:
                tx_node = GraphTransaction(
                    uid=str(sql_obj.transaction.pk),
                    amount=float(sql_obj.transaction.amount),
                    currency=sql_obj.transaction.currency.symbol,
                    timestamp=sql_obj.transaction.timestamp,
                    metadata={"source": "finance"},
                ).save()
            if not node.transaction.is_connected(tx_node):
                node.transaction.connect(tx_node)

        return node

    def delete(self, sql_obj):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj):
        return GraphFundingRound.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphFundingRound.nodes.all()
