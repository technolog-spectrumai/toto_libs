from django.contrib import admin
from .models import (
    Company,
    Shareholder,
    Chamber,
    FundingRound
)
from yamabiko.admin import BaseSerializableAdmin


@admin.register(Chamber)
class ChamberAdmin(BaseSerializableAdmin):
    list_display = ('name', 'default_currency', 'active')
    list_filter = ('active', 'default_currency')
    search_fields = ('name',)


@admin.register(Company)
class CompanyAdmin(BaseSerializableAdmin):
    list_display = (
        'name', 'registration_number', 'country', 'industry',
        'date_founded', 'is_active', 'has_social_entity', 'has_subject'
    )
    list_filter = ('country', 'industry', 'is_active')
    search_fields = ('name', 'registration_number')

    def has_social_entity(self, obj):
        return bool(obj.social_entity)
    has_social_entity.boolean = True
    has_social_entity.short_description = "SocialEntity Linked"

    def has_subject(self, obj):
        return hasattr(obj.social_entity, "subject") if obj.social_entity else False
    has_subject.boolean = True
    has_subject.short_description = "Finance Subject"


@admin.register(Shareholder)
class ShareholderAdmin(BaseSerializableAdmin):
    list_display = (
        'full_name',
        'email',
        'shares_owned',
        'company',
        'date_joined',
        'is_active',
        'identity_type',   # new column
    )
    list_filter = ('is_active', 'company')
    search_fields = ('full_name', 'email', 'company__name', 'social_entity__name')

    def identity_type(self, obj):
        """
        Show the inferred identity type from SocialEntity.
        """
        return obj.get_identity_type()
    identity_type.short_description = "Identity Type"


@admin.register(FundingRound)
class FundingRoundAdmin(BaseSerializableAdmin):
    list_display = ('name', 'venture', 'amount', 'currency', 'timestamp', 'transaction')
    list_filter = ('currency', 'venture')  # ✅ venture is Company now
    search_fields = ('name', 'venture__name')  # ✅ no nested company
