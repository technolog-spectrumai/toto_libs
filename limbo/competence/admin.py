from django.contrib import admin

from toto.people.admin import PersonAdmin
from toto.people.models import Person

from .models import Experience, SkillBadge, SkillBadgePrerequisite, SkillGroup


class ExperienceInline(admin.TabularInline):
    model = Experience
    extra = 0
    fields = ("title", "institution", "place", "started_at", "ended_at", "is_current", "order")
    ordering = ("order", "-started_at")


@admin.register(Experience)
class ExperienceAdmin(admin.ModelAdmin):
    list_display = ("title", "person", "institution", "place", "started_at", "ended_at", "is_current", "order")
    list_filter = ("is_current", "institution", "started_at")
    search_fields = ("title", "institution", "place", "description", "person__display_name")
    autocomplete_fields = ("person",)


try:
    admin.site.unregister(Person)
except admin.sites.NotRegistered:
    pass


@admin.register(Person)
class PersonAdminWithExperiences(PersonAdmin):
    inlines = [ExperienceInline]


class SkillBadgeInline(admin.TabularInline):
    model = SkillBadge
    extra = 0
    fields = (
        "title",
        "slug",
        "icon",
        "order",
    )
    prepopulated_fields = {
        "slug": ("title",),
    }


class SkillBadgePrerequisiteInline(admin.TabularInline):
    model = SkillBadgePrerequisite
    fk_name = "badge"
    extra = 0
    autocomplete_fields = (
        "prerequisite",
    )


@admin.register(SkillGroup)
class SkillGroupAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "order",
    )
    list_editable = (
        "order",
    )
    search_fields = (
        "title",
        "description",
    )
    prepopulated_fields = {
        "slug": ("title",),
    }
    inlines = [
        SkillBadgeInline,
    ]


@admin.register(SkillBadge)
class SkillBadgeAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "group",
        "slug",
        "order",
    )
    list_filter = (
        "group",
    )
    list_editable = (
        "order",
    )
    search_fields = (
        "title",
        "description",
        "group__title",
    )
    prepopulated_fields = {
        "slug": ("title",),
    }
    autocomplete_fields = (
        "group",
    )
    inlines = [
        SkillBadgePrerequisiteInline,
    ]


@admin.register(SkillBadgePrerequisite)
class SkillBadgePrerequisiteAdmin(admin.ModelAdmin):
    list_display = (
        "prerequisite",
        "badge",
    )
    search_fields = (
        "badge__title",
        "prerequisite__title",
    )
    autocomplete_fields = (
        "badge",
        "prerequisite",
    )
