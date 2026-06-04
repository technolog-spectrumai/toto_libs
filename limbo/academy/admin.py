from django.contrib import admin
from toto.competence.models import SkillBadge
from toto.quizzes.models import Quiz
from toto.verbena.admin import SectionInlineMixin, PageAdminMixin

from .models import (
    Certificate,
    Cohort,
    CohortMembership,
    Course,
    CourseEnrollment,
    CourseModule,
    LearningPath,
    LearningPathBadge,
    Lesson,
    Script,
    ScriptSection,
    Student,
    StudentBadge,
    Teacher,
)


class ScriptSectionInline(SectionInlineMixin):
    model = ScriptSection
    fields = ["title", "content", "author", "order"]


class ScriptInline(admin.TabularInline):
    model = Script
    extra = 0
    fields = ("title", "slug", "author")
    prepopulated_fields = {"slug": ("title",)}
    show_change_link = True


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 0
    fields = (
        "title",
        "slug",
        "owner",
        "lecture",
        "attached_quizzes",
        "order",
    )
    prepopulated_fields = {
        "slug": ("title",),
    }
    autocomplete_fields = (
        "lecture",
        "owner",
        "attached_quizzes",
    )


class CourseModuleInline(admin.TabularInline):
    model = CourseModule
    extra = 0
    fields = (
        "title",
        "slug",
        "unlocks_badge",
        "owner",
        "verbena_page",
        "exam",
        "attached_quizzes",
        "order",
    )
    prepopulated_fields = {
        "slug": ("title",),
    }
    autocomplete_fields = (
        "unlocks_badge",
        "owner",
        "verbena_page",
        "exam",
        "attached_quizzes",
    )


class LearningPathBadgeInline(admin.TabularInline):
    model = LearningPathBadge
    extra = 0
    fields = (
        "badge",
        "order",
        "note",
    )
    autocomplete_fields = (
        "badge",
    )


class StudentBadgeInline(admin.TabularInline):
    model = StudentBadge
    extra = 0
    autocomplete_fields = (
        "badge",
    )
    readonly_fields = (
        "awarded_at",
    )


class CourseEnrollmentInline(admin.TabularInline):
    model = CourseEnrollment
    extra = 0
    autocomplete_fields = (
        "course",
    )
    readonly_fields = (
        "enrolled_at",
    )


class CertificateInline(admin.TabularInline):
    model = Certificate
    extra = 0
    fields = (
        "uuid",
        "title",
        "course",
        "exam",
        "granted_at",
    )
    readonly_fields = (
        "uuid",
    )
    autocomplete_fields = (
        "course",
        "exam",
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "exam":
            kwargs["queryset"] = Quiz.objects.filter(is_official=True).order_by("title")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "title",
        "is_active",
        "created_at",
    )
    list_filter = (
        "is_active",
        "created_at",
    )
    search_fields = (
        "person__display_name",
        "person__email",
        "title",
        "bio",
    )
    autocomplete_fields = (
        "person",
    )
    readonly_fields = (
        "created_at",
    )


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "author",
        "owner",
        "is_published",
        "is_virtual",
        "module_count",
        "lesson_count",
        "order",
        "updated_at",
    )
    list_filter = (
        "is_published",
        "is_virtual",
        "created_at",
        "updated_at",
    )
    list_editable = (
        "is_published",
        "is_virtual",
        "order",
    )
    search_fields = (
        "title",
        "description",
        "author__username",
        "owner__person__display_name",
    )
    prepopulated_fields = {
        "slug": ("title",),
    }
    autocomplete_fields = (
        "author",
        "owner",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )
    inlines = [
        CourseModuleInline,
    ]


@admin.register(CourseModule)
class CourseModuleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "course",
        "owner",
        "unlocks_badge",
        "verbena_page",
        "exam",
        "slug",
        "order",
    )
    list_filter = (
        "course",
        "unlocks_badge__group",
        "owner",
    )
    list_editable = (
        "order",
    )
    search_fields = (
        "title",
        "description",
        "course__title",
        "owner__person__display_name",
        "unlocks_badge__title",
        "verbena_page__title",
    )
    prepopulated_fields = {
        "slug": ("title",),
    }
    autocomplete_fields = (
        "course",
        "owner",
        "unlocks_badge",
        "verbena_page",
        "exam",
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "exam":
            kwargs["queryset"] = Quiz.objects.filter(is_official=True).order_by("title")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    inlines = [
        LessonInline,
        ScriptInline,
    ]


@admin.register(Script)
class ScriptAdmin(PageAdminMixin):
    list_display = ("title", "module", "author", "created_at")
    list_filter = ("module__course",)
    search_fields = ["title", "description", "module__title", "module__course__title"]
    autocomplete_fields = ("module", "author")
    readonly_fields = ["created_at"]
    inlines = [ScriptSectionInline]


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "module",
        "owner",
        "lecture",
        "has_video",
        "slug",
        "quiz_count",
        "order",
    )
    list_filter = (
        "module__course",
        "owner",
    )
    list_editable = (
        "order",
    )
    search_fields = (
        "title",
        "summary",
        "module__title",
        "module__course__title",
        "owner__person__display_name",
        "lecture__title",
    )
    prepopulated_fields = {
        "slug": ("title",),
    }
    autocomplete_fields = (
        "module",
        "owner",
        "lecture",
        "attached_quizzes",
    )
    raw_id_fields = ("video_file",)

    @admin.display(description="Video", boolean=True)
    def has_video(self, obj):
        return obj.video_file_id is not None

    @admin.display(description="Quizzes")
    def quiz_count(self, obj):
        return obj.attached_quizzes.count()


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "person",
        "created_at",
    )
    search_fields = (
        "person__name",
    )
    autocomplete_fields = (
        "person",
    )
    readonly_fields = (
        "created_at",
    )
    inlines = [
        StudentBadgeInline,
        CourseEnrollmentInline,
    ]


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = (
        "display_title",
        "person",
        "course",
        "exam",
        "uuid",
        "granted_at",
    )
    list_filter = (
        "course",
        "exam",
        "granted_at",
    )
    search_fields = (
        "title",
        "description",
        "person__display_name",
        "course__title",
        "exam__title",
        "uuid",
    )
    autocomplete_fields = (
        "person",
        "course",
        "exam",
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "exam":
            kwargs["queryset"] = Quiz.objects.filter(is_official=True).order_by("title")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    readonly_fields = (
        "uuid",
    )


@admin.register(StudentBadge)
class StudentBadgeAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "badge",
        "awarded_at",
    )
    list_filter = (
        "badge__group",
    )
    search_fields = (
        "student__person__name",
        "badge__title",
    )
    autocomplete_fields = (
        "student",
        "badge",
    )
    readonly_fields = (
        "awarded_at",
    )


@admin.register(CourseEnrollment)
class CourseEnrollmentAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "course",
        "enrolled_at",
        "completed_at",
    )
    list_filter = (
        "course",
    )
    search_fields = (
        "student__person__name",
        "course__title",
    )
    autocomplete_fields = (
        "student",
        "course",
    )
    readonly_fields = (
        "enrolled_at",
    )


@admin.register(LearningPath)
class LearningPathAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "is_published",
        "order",
        "updated_at",
    )
    list_filter = (
        "is_published",
        "created_at",
        "updated_at",
    )
    list_editable = (
        "is_published",
        "order",
    )
    search_fields = (
        "title",
        "description",
    )
    prepopulated_fields = {
        "slug": ("title",),
    }
    readonly_fields = (
        "created_at",
        "updated_at",
    )
    inlines = [
        LearningPathBadgeInline,
    ]


@admin.register(LearningPathBadge)
class LearningPathBadgeAdmin(admin.ModelAdmin):
    list_display = (
        "learning_path",
        "badge",
        "order",
    )
    list_filter = (
        "learning_path",
        "badge__group",
    )
    list_editable = (
        "order",
    )
    search_fields = (
        "learning_path__title",
        "badge__title",
        "note",
    )
    autocomplete_fields = (
        "learning_path",
        "badge",
    )


# Cohorts
# ────────────────────────────────────────────────

class CohortMembershipInline(admin.TabularInline):
    model = CohortMembership
    extra = 0
    autocomplete_fields = ("student",)
    readonly_fields = ("joined_at",)


@admin.register(Cohort)
class CohortAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "teacher", "member_count", "capacity", "is_active", "starts_at", "ends_at")
    list_filter = ("is_active", "course", "teacher")
    list_editable = ("is_active",)
    search_fields = ("title", "course__title", "teacher__person__display_name")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("course", "teacher")
    readonly_fields = ()
    inlines = [CohortMembershipInline]

    @admin.display(description="Members")
    def member_count(self, obj):
        return obj.memberships.count()


@admin.register(CohortMembership)
class CohortMembershipAdmin(admin.ModelAdmin):
    list_display = ("student", "cohort", "joined_at")
    list_filter = ("cohort__course", "cohort")
    search_fields = ("student__person__display_name", "cohort__title", "cohort__course__title")
    autocomplete_fields = ("cohort", "student")
    readonly_fields = ("joined_at",)
