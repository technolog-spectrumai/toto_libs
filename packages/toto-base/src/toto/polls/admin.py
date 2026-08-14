"""The operator's view of a question — read-mostly on purpose.

Ballots are readonly here and cannot be added. A vote whose record can be
edited in the admin is not a record, and the whole reason Business Center's
resolutions can live on this engine is that the ballot table is append-only in
practice as well as in principle.
"""

from django.contrib import admin

from .models import Ballot, Choice, Question


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 2


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "status", "scope_type", "scope_id",
                    "opens_at", "closes_at")
    list_filter = ("kind", "status", "scope_type", "revisability", "visibility")
    search_fields = ("title", "question_text", "slug", "scope_id")
    readonly_fields = ("created_at", "updated_at", "closed_at", "slug")
    inlines = [ChoiceInline]


@admin.register(Ballot)
class BallotAdmin(admin.ModelAdmin):
    list_display = ("question", "voter", "choice", "weight", "cast_at",
                    "revisions")
    list_filter = ("question__kind",)
    search_fields = ("voter__username", "question__title")
    readonly_fields = [f.name for f in Ballot._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
