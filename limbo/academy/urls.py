from django.conf import settings
from django.urls import path
from django.views.generic.base import RedirectView

from . import views as academy_views
from .views import (
    CourseDetailView,
    CourseListView,
    ScriptDetailView,
    SkillForestView,
    SkillTreeDetailView,
    StudentProgressView,
    TeacherDetailView,
    certificate_detail,
    certificate_sign,
)

app_name = "academy"

urlpatterns = [
    path(
        "",
        RedirectView.as_view(
            url="courses/",
            permanent=not settings.DEBUG,
        ),
    ),

    # Courses
    path(
        "courses/",
        CourseListView.as_view(),
        name="course-list",
    ),
    path(
        "courses/<slug:slug>/",
        CourseDetailView.as_view(),
        name="course-detail",
    ),
    path(
        "progress/",
        StudentProgressView.as_view(),
        name="student-progress",
    ),
    path(
        "teachers/<int:pk>/",
        TeacherDetailView.as_view(),
        name="teacher-detail",
    ),

    # Scripts
    path(
        "scripts/<int:pk>/",
        ScriptDetailView.as_view(),
        name="script-detail",
    ),

    # Course metrics
    path(
        "courses/<slug:slug>/metrics/",
        academy_views.course_metrics,
        name="course-metrics",
    ),

    # Skill forest / trees
    path(
        "skills/",
        SkillForestView.as_view(),
        name="skill-forest",
    ),
    path(
        "skills/<slug:slug>/",
        SkillTreeDetailView.as_view(),
        name="skill-tree-detail",
    ),

    # Self-enrollment (subscription-gated)
    path(
        "courses/<slug:slug>/enroll/",
        academy_views.course_enroll,
        name="course-enroll",
    ),

    # Certificates
    path(
        "certificates/<uuid:uuid>/",
        certificate_detail,
        name="certificate-detail",
    ),
    path(
        "certificates/<uuid:uuid>/sign/",
        certificate_sign,
        name="certificate-sign",
    ),
]
