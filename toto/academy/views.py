import json

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.generic import TemplateView
from django.views.generic import DetailView, ListView

from toto.competence.models import SkillBadge, SkillGroup
from toto.ui import PageProcessor
from toto.verbena.views import PageDetailMixin

from .models import (
    Certificate, Cohort, Course, CourseModule, Script, Student, Teacher,
)


def build_skill_tree_graph(tree):
    badges = list(
        tree.badges
        .select_related("group")
        .prefetch_related(
            "prerequisites",
            "unlocking_modules",
            "unlocking_modules__course",
        )
        .order_by("order", "title")
    )
    badge_ids = {badge.pk for badge in badges}

    nodes = [
        {
            "data": {
                "id": f"group-{tree.pk}",
                "label": tree.title,
                "kind": "group",
                "description": tree.description,
                "url": reverse("academy:skill-tree-detail", kwargs={"slug": tree.slug}),
            }
        }
    ]
    edges = []

    for badge in badges:
        courses = []
        seen_course_ids = set()

        for module in badge.unlocking_modules.all():
            course = module.course
            if course.pk in seen_course_ids:
                continue

            seen_course_ids.add(course.pk)
            courses.append(
                {
                    "title": course.title,
                    "url": course.get_absolute_url(),
                }
            )

        nodes.append(
            {
                "data": {
                    "id": f"badge-{badge.pk}",
                    "label": badge.title,
                    "kind": "badge",
                    "description": badge.description,
                    "icon": badge.icon,
                    "courses": courses,
                    "course_count": len(courses),
                }
            }
        )

        if not badge.prerequisites.exists():
            edges.append(
                {
                    "data": {
                        "id": f"group-{tree.pk}-badge-{badge.pk}",
                        "source": f"group-{tree.pk}",
                        "target": f"badge-{badge.pk}",
                    }
                }
            )

        for prerequisite in badge.prerequisites.all():
            if prerequisite.pk not in badge_ids:
                continue

            edges.append(
                {
                    "data": {
                        "id": f"badge-{prerequisite.pk}-badge-{badge.pk}",
                        "source": f"badge-{prerequisite.pk}",
                        "target": f"badge-{badge.pk}",
                    }
                }
            )

    return {
        "tree": {
            "id": tree.pk,
            "title": tree.title,
            "slug": tree.slug,
            "description": tree.description,
            "url": reverse("academy:skill-tree-detail", kwargs={"slug": tree.slug}),
            "badge_count": len(badges),
        },
        "elements": nodes + edges,
    }


def build_student_progress_graph(student):
    earned_badge_ids = set(
        student.badges.values_list("id", flat=True)
    ) if student else set()
    enrolled_course_ids = set(
        student.enrolled_courses.values_list("id", flat=True)
    ) if student else set()

    groups = (
        SkillGroup.objects
        .prefetch_related(
            "badges",
            "badges__prerequisites",
            "badges__unlocking_modules",
            "badges__unlocking_modules__course",
        )
        .order_by("order", "title")
    )

    elements = []

    for group in groups:
        group_id = f"group-{group.pk}"
        elements.append(
            {
                "data": {
                    "id": group_id,
                    "label": group.title,
                    "kind": "group",
                    "status": "group",
                    "url": reverse("academy:skill-tree-detail", kwargs={"slug": group.slug}),
                }
            }
        )

        for badge in group.badges.all().order_by("order", "title"):
            prerequisite_ids = set(badge.prerequisites.values_list("id", flat=True))
            same_group_prerequisites = [
                prerequisite
                for prerequisite in badge.prerequisites.all()
                if prerequisite.group_id == group.pk
            ]
            module_courses = []
            module_course_ids = set()

            for module in badge.unlocking_modules.all():
                course = module.course
                if course.pk in module_course_ids:
                    continue

                module_course_ids.add(course.pk)
                module_courses.append(
                    {
                        "title": course.title,
                        "url": course.get_absolute_url(),
                    }
                )

            if badge.pk in earned_badge_ids:
                status = "earned"
            elif prerequisite_ids.issubset(earned_badge_ids):
                status = "available"
            else:
                status = "locked"

            if status == "available" and enrolled_course_ids.intersection(module_course_ids):
                status = "current"

            elements.append(
                {
                    "data": {
                        "id": f"badge-{badge.pk}",
                        "label": badge.title,
                        "kind": "badge",
                        "status": status,
                        "description": badge.description,
                        "courses": module_courses,
                    }
                }
            )

            if not same_group_prerequisites:
                elements.append(
                    {
                        "data": {
                            "id": f"{group_id}-badge-{badge.pk}",
                            "source": group_id,
                            "target": f"badge-{badge.pk}",
                        }
                    }
                )

            for prerequisite in same_group_prerequisites:
                elements.append(
                    {
                        "data": {
                            "id": f"badge-{prerequisite.pk}-badge-{badge.pk}",
                            "source": f"badge-{prerequisite.pk}",
                            "target": f"badge-{badge.pk}",
                        }
                    }
                )

    return {
        "elements": elements,
        "summary": {
            "earned": len(earned_badge_ids),
            "enrolled": len(enrolled_course_ids),
        },
    }


class AcademyContextMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class CourseListView(AcademyContextMixin, ListView):
    model = Course
    template_name = "academy/course_list.html"
    context_object_name = "courses"

    def get_queryset(self):
        queryset = (
            Course.objects
            .select_related("author", "owner", "owner__person")
            .prefetch_related(
                "modules",
                "modules__lessons",
                "modules__unlocks_badge",
            )
            .filter(is_virtual=False)
        )

        if not self.request.user.is_staff:
            queryset = queryset.filter(is_published=True)

        return queryset.order_by("order", "title")


class TeacherDetailView(AcademyContextMixin, DetailView):
    model = Teacher
    template_name = "academy/teacher_detail.html"
    context_object_name = "teacher"

    def get_queryset(self):
        return Teacher.objects.select_related("person")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        teacher = self.object

        context["courses"] = (
            teacher.owned_courses
            .prefetch_related("modules", "modules__lessons")
            .order_by("order", "title")
        )
        context["modules"] = (
            teacher.owned_modules
            .select_related("course")
            .order_by("course__order", "course__title", "order", "title")
        )
        context["lessons"] = (
            teacher.owned_lessons
            .select_related("module", "module__course", "lecture")
            .order_by("module__course__order", "module__order", "order", "title")
        )
        context["certificates"] = (
            Certificate.objects
            .filter(person=teacher.person)
            .select_related("course")
            .order_by("-granted_at")
        )
        context["experiences"] = teacher.person.experiences.all()

        return context


class StudentProgressView(LoginRequiredMixin, AcademyContextMixin, TemplateView):
    template_name = "academy/student_progress.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        person = getattr(self.request.user, "community_profile", None)
        student = None

        if person:
            student, _ = Student.objects.get_or_create(person=person)

        earned_badges = SkillBadge.objects.none()
        enrolled_courses = Course.objects.none()
        certificates = Certificate.objects.none()

        if student:
            earned_badges = (
                student.badges
                .select_related("group")
                .order_by("group__order", "order", "title")
            )
            enrolled_courses = (
                student.enrolled_courses
                .order_by("order", "title")
            )
            certificates = (
                Certificate.objects
                .filter(person=student.person)
                .select_related("course")
                .order_by("-granted_at")
            )

        context["person"] = person
        context["student"] = student
        context["earned_badges"] = earned_badges
        context["enrolled_courses"] = enrolled_courses
        context["certificates"] = certificates
        context["progress_graph"] = build_student_progress_graph(student)

        return context


class CourseDetailView(AcademyContextMixin, DetailView):
    model = Course
    template_name = "academy/course_detail.html"
    context_object_name = "course"

    def get_queryset(self):
        queryset = (
            Course.objects
            .select_related("author", "owner", "owner__person")
            .prefetch_related(
                "modules",
                "modules__lessons",
                "modules__lessons__lecture",
                "modules__unlocks_badge",
                "modules__unlocks_badge__group",
                "modules__unlocks_badge__prerequisites",
                "modules__verbena_page",
                "modules__attached_quizzes",
                "modules__owner",
                "modules__owner__person",
                "modules__lessons__owner",
                "modules__lessons__owner__person",
            )
        )

        if not self.request.user.is_staff:
            queryset = queryset.filter(is_published=True)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        course = self.object

        modules = (
            CourseModule.objects
            .filter(course=course)
            .select_related(
                "unlocks_badge",
                "unlocks_badge__group",
                "verbena_page",
                "owner",
                "owner__person",
            )
            .prefetch_related(
                "lessons",
                "lessons__lecture",
                "lessons__owner",
                "lessons__owner__person",
                "attached_quizzes",
                "attached_quizzes__questions",
                "unlocks_badge__prerequisites",
            )
            .order_by("order", "id")
        )
        lessons = []

        for module in modules:
            for lesson in module.lessons.all().order_by("order", "id"):
                lessons.append(lesson)

        badges = SkillBadge.objects.filter(
            unlocking_modules__course=course
        ).select_related("group").prefetch_related(
            "prerequisites"
        ).distinct().order_by("group__order", "order", "title")

        skill_groups = SkillGroup.objects.filter(
            badges__unlocking_modules__course=course
        ).distinct().order_by("order", "title")

        context["modules"] = modules
        context["lessons"] = lessons
        context["badges"] = badges
        context["skill_groups"] = skill_groups
        context["badge_count"] = badges.count()
        context["lesson_count"] = len(lessons)

        return context


class SkillForestView(AcademyContextMixin, ListView):
    model = SkillGroup
    template_name = "academy/skill_forest.html"
    context_object_name = "trees"

    def get_queryset(self):
        return (
            SkillGroup.objects
            .prefetch_related(
                "badges",
                "badges__prerequisites",
                "badges__unlocking_modules",
                "badges__unlocking_modules__course",
            )
            .order_by("order", "title")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        trees = list(context["trees"])
        context["tree_graphs"] = [build_skill_tree_graph(tree) for tree in trees]
        return context


class SkillTreeDetailView(AcademyContextMixin, DetailView):
    model = SkillGroup
    template_name = "academy/skill_tree_detail.html"
    context_object_name = "tree"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return (
            SkillGroup.objects
            .prefetch_related(
                "badges",
                "badges__prerequisites",
                "badges__unlocking_modules",
                "badges__unlocking_modules__course",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        tree = self.object

        context["badges"] = (
            tree.badges
            .select_related("group")
            .prefetch_related(
                "prerequisites",
                "unlocking_modules",
                "unlocking_modules__course",
            )
            .order_by("order", "title")
        )
        context["tree_graph"] = build_skill_tree_graph(tree)

        return context


class ScriptDetailView(PageDetailMixin, DetailView):
    model = Script
    template_name = "academy/script_detail.html"
    context_object_name = "page"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sections"] = self.render_sections(self.object)
        context["back_url"] = "academy:course-detail"
        context["back_url_slug"] = self.object.module.course.slug
        context["back_label"] = self.object.module.course.title
        context["page_type_label"] = "Script"
        return PageProcessor().decorate(context, self.request)


@login_required
def course_metrics(request, slug):
    course = get_object_or_404(
        Course.objects.prefetch_related(
            "modules",
            "modules__attached_quizzes",
            "cohorts",
            "cohorts__teacher",
            "cohorts__memberships__student__person",
            "students",
            "students__person",
            "course_enrollments__student__person",
        ),
        slug=slug,
    )

    person = getattr(request.user, "community_profile", None)
    is_teacher = person and Teacher.objects.filter(person=person).exists()
    if not is_teacher:
        from django.http import Http404
        raise Http404

    total_enrolled = course.course_enrollments.count()
    total_completed = course.course_enrollments.filter(completed_at__isnull=False).count()

    # Per-module quiz attempt rates
    module_stats = []
    for module in course.modules.all():
        quizzes = list(module.attached_quizzes.filter(is_published=True))
        quiz_rows = []
        for quiz in quizzes:
            attempts = quiz.attempts.filter(completed_at__isnull=False).count()
            quiz_rows.append({"quiz": quiz, "completed_attempts": attempts})
        module_stats.append({
            "module": module,
            "quiz_rows": quiz_rows,
        })

    # Cohort breakdown
    cohort_stats = []
    for cohort in course.cohorts.all():
        member_ids = set(cohort.memberships.values_list("student_id", flat=True))
        completed = course.course_enrollments.filter(
            student_id__in=member_ids,
            completed_at__isnull=False,
        ).count()
        cohort_stats.append({
            "cohort": cohort,
            "member_count": len(member_ids),
            "completed": completed,
            "pct": round(completed / len(member_ids) * 100) if member_ids else 0,
        })

    enrollment_chart = json.dumps({
        "chart_type": "doughnut",
        "labels": ["Completed", "In progress"],
        "datasets": [{
            "data": [total_completed, max(total_enrolled - total_completed, 0)],
            "backgroundColor": ["#10B981", "#4F46E5"],
        }],
    })

    context = {
        "course": course,
        "total_enrolled": total_enrolled,
        "total_completed": total_completed,
        "module_stats": module_stats,
        "cohort_stats": cohort_stats,
        "enrollment_chart_json": enrollment_chart,
    }
    return render(request, "academy/course_metrics.html", PageProcessor().decorate(context, request))
