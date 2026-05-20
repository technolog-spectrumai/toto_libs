from django.contrib import admin
from .models import BountyBoard, BountyCategory, Bounty, BountyClaim, BountySubmission, BountyReview, BountyPayment


class BountyCategoryInline(admin.TabularInline):
    model = BountyCategory
    extra = 1


@admin.register(BountyBoard)
class BountyBoardAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'shop', 'currency', 'is_active')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [BountyCategoryInline]


@admin.register(Bounty)
class BountyAdmin(admin.ModelAdmin):
    list_display = ('title', 'board', 'bounty_type', 'status', 'reward_amount', 'reward_currency', 'deadline', 'is_public')
    list_filter = ('board', 'bounty_type', 'status', 'is_public')
    search_fields = ('title', 'summary', 'description')
    prepopulated_fields = {'slug': ('title',)}


@admin.register(BountyClaim)
class BountyClaimAdmin(admin.ModelAdmin):
    list_display = ('bounty', 'hunter', 'status', 'created_at', 'accepted_at', 'completed_at')
    list_filter = ('status',)
    search_fields = ('bounty__title',)


for model in [BountyCategory, BountySubmission, BountyReview, BountyPayment]:
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass
