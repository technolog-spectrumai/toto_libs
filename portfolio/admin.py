from django.contrib import admin
from .models import (
    Chamber,
    Company,
    SharePackage,
    FundingRound,
)
from toto.admin import BaseSerializableAdmin
from portfolio.graph.sync import (
    CompanyConversionStrategy,
    SharePackageConversionStrategy,
    FundingRoundConversionStrategy,
)


@admin.register(Chamber)
class ChamberAdmin(BaseSerializableAdmin):
    list_display = ('name', 'default_currency', 'active')
    list_filter = ('active', 'default_currency')
    search_fields = ('name',)


@admin.register(Company)
class CompanyAdmin(BaseSerializableAdmin):
    strategy_class = CompanyConversionStrategy
    strategy_label = "Companies"
    actions = BaseSerializableAdmin.actions + ['sync_with_neo4j']

    list_display = (
        'name', 'registration_number', 'country', 'industry',
        'date_founded', 'is_active', 'identity_type'
    )
    list_filter = ('country', 'industry', 'is_active')
    search_fields = ('name', 'registration_number')

    def identity_type(self, obj):
        return obj.get_real_instance_class().__name__
    identity_type.short_description = "Identity Type"


@admin.register(SharePackage)
class SharePackageAdmin(BaseSerializableAdmin):
    strategy_class = SharePackageConversionStrategy
    strategy_label = "Shares"

    list_display = (
        'get_full_name',
        'shares_owned',
        'company',
        'date_joined',
        'is_active',
        #'identity_type',
    )
    list_filter = ('is_active', 'company')
    search_fields = (
        'company__name',
        'social_entity__id',
        'social_entity__member__display_name',
        'social_entity__company__name',
        'social_entity__community__name',
    )

    def identity_type(self, obj):
        return obj.get_real_instance_class().__name__
    identity_type.short_description = "Identity Type"

    def get_full_name(self, obj):
        return obj.get_full_name()
    get_full_name.short_description = "Full Name"


@admin.register(FundingRound)
class FundingRoundAdmin(BaseSerializableAdmin):
    strategy_class = FundingRoundConversionStrategy
    strategy_label = "Funding Rounds"

    list_display = ('name', 'venture', 'amount', 'currency', 'timestamp', 'transaction')
    list_filter = ('currency', 'venture')
    search_fields = ('name', 'venture__name')
