from django.contrib import admin
from .models import Question, Choice, Answer


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 1
    show_change_link = True
    fields = ("label", "choice_text", "value")
    ordering = ("label",)


class AnswerInline(admin.TabularInline):
    model = Answer
    extra = 0
    readonly_fields = ("answered_at",)
    show_change_link = True
    fields = ("user", "choice", "answered_at")
    autocomplete_fields = ("user", "choice")


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "pub_date", "was_published_recently")
    list_filter = ("is_active", "pub_date")
    search_fields = ("name", "question_text")
    list_editable = ("is_active",)
    inlines = [ChoiceInline, AnswerInline]
    ordering = ("-pub_date",)


@admin.register(Choice)
class ChoiceAdmin(admin.ModelAdmin):
    list_display = ("label", "choice_text", "value", "question")
    list_filter = ("question",)
    search_fields = ("label", "choice_text")
    autocomplete_fields = ("question",)
    ordering = ("question", "label")


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ("user", "question", "choice", "answered_at")
    list_filter = ("question", "user")
    search_fields = ("user__username", "question__name", "choice__label")
    autocomplete_fields = ("user", "question", "choice")
    readonly_fields = ("answered_at",)
    ordering = ("-answered_at",)
