from django.contrib import admin

from .models import (
    Quiz,
    QuizAnswer,
    QuizAnswerTrait,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizQuestion,
    QuizTrait,
)


class QuizAnswerTraitInline(admin.TabularInline):
    model = QuizAnswerTrait
    extra = 0
    autocomplete_fields = ("trait",)


@admin.register(QuizTrait)
class QuizTraitAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "icon", "order")
    list_editable = ("order",)
    search_fields = ("title", "description")
    prepopulated_fields = {"slug": ("title",)}


class QuizAnswerInline(admin.TabularInline):
    model = QuizAnswer
    extra = 0
    fields = ("text", "is_correct", "order")
    ordering = ("order",)
    show_change_link = True


class QuizQuestionInline(admin.StackedInline):
    model = QuizQuestion
    extra = 0
    fields = ("text", "explanation", "is_multiple_choice", "order")
    ordering = ("order",)
    show_change_link = True


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "is_published", "is_official", "order", "updated_at")
    list_filter = ("is_published", "is_official")
    list_editable = ("is_published", "is_official", "order")
    search_fields = ("title", "description", "owner__display_name")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [QuizQuestionInline]


@admin.register(QuizQuestion)
class QuizQuestionAdmin(admin.ModelAdmin):
    list_display = ("text_preview", "quiz", "is_multiple_choice", "max_time", "order")
    list_filter = ("is_multiple_choice", "quiz")
    search_fields = ("text", "explanation", "quiz__title")
    autocomplete_fields = ("quiz",)
    inlines = [QuizAnswerInline]

    @admin.display(description="Question")
    def text_preview(self, obj):
        return obj.text[:80]


@admin.register(QuizAnswer)
class QuizAnswerAdmin(admin.ModelAdmin):
    list_display = ("text_preview", "question", "is_correct", "order")
    list_filter = ("is_correct",)
    search_fields = ("text", "explanation", "question__text", "question__quiz__title")
    autocomplete_fields = ("question",)
    inlines = [QuizAnswerTraitInline]

    @admin.display(description="Answer")
    def text_preview(self, obj):
        return obj.text[:80]


class QuizAttemptAnswerInline(admin.TabularInline):
    model = QuizAttemptAnswer
    extra = 0
    autocomplete_fields = ("question", "answer")
    readonly_fields = ("selected_at",)


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ("participant", "quiz", "started_at", "completed_at")
    list_filter = ("quiz",)
    search_fields = ("participant__display_name", "quiz__title")
    autocomplete_fields = ("participant", "quiz")
    readonly_fields = ("started_at",)
    inlines = [QuizAttemptAnswerInline]
