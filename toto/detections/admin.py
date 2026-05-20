from django.contrib import admin
from .models import (
    DetectionCategory, Detection, DetectionHandle,
    BountyBoard, Bounty, BountyClaim, BountySubmission, BountyReview, BountyPayment,
)


@admin.register(DetectionCategory)
class DetectionCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'parent', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}


class DetectionHandleInline(admin.TabularInline):
    model = DetectionHandle
    extra = 0
    fields = ('assigned_to', 'status', 'note')


@admin.register(Detection)
class DetectionAdmin(admin.ModelAdmin):
    list_display = ('title', 'detection_type', 'severity', 'status', 'reported_by', 'start_time', 'location_label')
    list_filter = ('detection_type', 'severity', 'status', 'category')
    search_fields = ('title', 'description')
    filter_horizontal = ('involved_persons',)
    inlines = [DetectionHandleInline]
    readonly_fields = ('created_at', 'updated_at')

    def location_label(self, obj):
        return obj.location_label
    location_label.short_description = 'Location'


@admin.register(DetectionHandle)
class DetectionHandleAdmin(admin.ModelAdmin):
    list_display = ('detection', 'assigned_to', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('detection__title',)


@admin.register(BountyBoard)
class BountyBoardAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'shop', 'currency', 'is_active')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Bounty)
class BountyAdmin(admin.ModelAdmin):
    list_display = ('title', 'board', 'bounty_type', 'status', 'reward_amount', 'deadline', 'is_public')
    list_filter = ('board', 'bounty_type', 'status', 'is_public')
    search_fields = ('title', 'summary', 'description')
    prepopulated_fields = {'slug': ('title',)}


@admin.register(BountyClaim)
class BountyClaimAdmin(admin.ModelAdmin):
    list_display = ('bounty', 'hunter', 'status', 'created_at', 'accepted_at', 'completed_at')
    list_filter = ('status',)
    search_fields = ('bounty__title',)


@admin.register(BountyPayment)
class BountyPaymentAdmin(admin.ModelAdmin):
    list_display = (
        'claim', 'amount', 'currency', 'asset', 'ledger_account',
        'receiver_ledger_account', 'is_settled', 'paid_at',
    )
    list_filter = ('is_settled', 'asset', 'currency')
    search_fields = ('claim__bounty__title', 'claim__hunter__display_name', 'ledger_tx_reference')


for model in [BountySubmission, BountyReview]:
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass
