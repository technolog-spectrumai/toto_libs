from django.contrib import admin
from .models import Poll, Option, Vote


class OptionInline(admin.TabularInline):
    model = Option
    extra = 1
    show_change_link = True
    fields = ("label", "option_text", "value")
    ordering = ("label",)


class VoteInline(admin.TabularInline):
    model = Vote
    extra = 0
    readonly_fields = ("voted_at",)
    show_change_link = True
    fields = ("user", "option", "voted_at")
    autocomplete_fields = ("user", "option")


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "is_active", "pub_date", "was_published_recently")
    list_filter = ("is_active", "pub_date")
    search_fields = ("name", "question_text")
    list_editable = ("is_active",)
    inlines = [OptionInline, VoteInline]
    ordering = ("-pub_date",)


@admin.register(Option)
class OptionAdmin(admin.ModelAdmin):
    list_display = ("label", "option_text", "value", "poll")
    list_filter = ("poll",)
    search_fields = ("label", "option_text")
    autocomplete_fields = ("poll",)
    ordering = ("poll", "label")


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ("user", "poll", "option", "voted_at")
    list_filter = ("poll", "user")
    search_fields = ("user__username", "poll__name", "option__label")
    autocomplete_fields = ("user", "poll", "option")
    readonly_fields = ("voted_at",)
    ordering = ("-voted_at",)
