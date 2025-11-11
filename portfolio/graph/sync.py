from finance.models import Transaction as SQLTransaction
from portfolio.models import Company as SQLCompany, SharePackage as SQLSharePackage, FundingRound as SQLFundingRound
from portfolio.graph.models import Company as GraphCompany, SharePackage as GraphSharePackage, FundingRound as GraphFundingRound
from finance.graph.models import Transaction as GraphTransaction
from toto.neo4j import ConversionStrategy
from community.graph.models import CommunityMember as GraphCommunityMember, Community as GraphCommunity
from community.models import CommunityMember as SQLCommunityMember, Community as SQLCommunity


class CompanyConversionStrategy(ConversionStrategy):
    def create(self, sql_obj: SQLCompany):
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

    def update(self, sql_obj: SQLCompany, node):
        node.social_id = str(sql_obj.id)
        node.name = sql_obj.name
        node.registration_number = sql_obj.registration_number
        node.country = sql_obj.country
        node.industry = sql_obj.industry
        node.date_founded = sql_obj.date_founded
        node.is_active = sql_obj.is_active
        node.save()
        return node

    def delete(self, sql_obj: SQLCompany):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj: SQLCompany):
        return GraphCompany.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphCompany.nodes.all()


class SharePackageConversionStrategy(ConversionStrategy):
    def create(self, sql_obj: SQLSharePackage):
        node = GraphSharePackage(
            uid=str(sql_obj.pk),
            social_id=str(sql_obj.social_entity.id),
            is_active=sql_obj.is_active,
            shares_owned=sql_obj.shares_owned,
            metadata={
                "date_joined": str(sql_obj.date_joined),
                "full_name": sql_obj.get_full_name(),
            },
        ).save()

        # connect to company
        company_node = GraphCompany.nodes.get_or_none(uid=str(sql_obj.company.pk))
        if company_node:
            node.company.connect(company_node)

        # connect to community member or community if applicable

        se_generic = sql_obj.social_entity
        se_real = se_generic.get_real_instance()
        if isinstance(se_real, SQLCommunityMember):
            cm_node = GraphCommunityMember.nodes.get_or_none(uid=str(sql_obj.social_entity.id))
            if cm_node:
                node.member.connect(cm_node)
        elif isinstance(se_real, SQLCommunity):
            c_node = GraphCommunity.nodes.get_or_none(uid=str(sql_obj.social_entity.id))
            if c_node:
                node.community.connect(c_node)

        return node

    def update(self, sql_obj: SQLSharePackage, node):
        node.social_id = str(sql_obj.social_entity.id)
        node.is_active = sql_obj.is_active
        node.shares_owned = sql_obj.shares_owned
        node.metadata = {
            "date_joined": str(sql_obj.date_joined),
            "full_name": sql_obj.get_full_name(),
        }
        node.save()

        # ensure company link
        company_node = GraphCompany.nodes.get_or_none(uid=str(sql_obj.company.pk))
        if company_node and not node.company.is_connected(company_node):
            node.company.connect(company_node)

        # ensure community member/community link
        se = sql_obj.social_entity
        se_real = se.get_real_instance()
        if isinstance(se_real, SQLCommunityMember):
            cm_node = GraphCommunityMember.nodes.get_or_none(uid=str(se.id))
            if cm_node and not node.member.is_connected(cm_node):
                node.member.connect(cm_node)
        elif isinstance(se_real, SQLCommunity):
            c_node = GraphCommunity.nodes.get_or_none(uid=str(se.id))
            if c_node and not node.community.is_connected(c_node):
                node.community.connect(c_node)

        return node

    def delete(self, sql_obj: SQLSharePackage):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj: SQLSharePackage):
        return GraphSharePackage.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphSharePackage.nodes.all()


class FundingRoundConversionStrategy(ConversionStrategy):
    def create(self, sql_obj: SQLFundingRound):
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
            node.venture.connect(company_node)

        # ✅ connect to transaction if exists
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

    def update(self, sql_obj: SQLFundingRound, node):
        node.name = sql_obj.name
        node.amount = float(sql_obj.amount)
        node.currency = sql_obj.currency.symbol
        node.timestamp = sql_obj.timestamp
        node.save()

        # ensure venture link
        company_node = GraphCompany.nodes.get_or_none(uid=str(sql_obj.venture.pk))
        if company_node and not node.venture.is_connected(company_node):
            node.venture.connect(company_node)

        # ✅ ensure transaction link
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

    def delete(self, sql_obj: SQLFundingRound):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj: SQLFundingRound):
        return GraphFundingRound.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphFundingRound.nodes.all()

