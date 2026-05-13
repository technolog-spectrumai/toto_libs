from django.contrib import admin
from toto.academy.models import Experience
from toto.core.base_admin import TotoModelAdmin


@admin.register(Experience)
class ExperienceAdmin(TotoModelAdmin):
    list_display = (
        "title",
        "person",
        "institution",
        "place",
        "started_at",
        "ended_at",
        "is_current",
        "order",
    )
    list_filter = (
        "is_current",
        "institution",
        "started_at",
    )
    search_fields = (
        "title",
        "institution",
        "place",
        "description",
        "person__display_name",
    )
    autocomplete_fields = ("person",)
