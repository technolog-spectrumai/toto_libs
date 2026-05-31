import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.urls import reverse

from toto.competence.models import SkillBadge
from toto.memo.models import MemoDeck
from toto.people.models import Person
from toto.verbena.models import AbstractPage, AbstractSection


class Teacher(models.Model):
    person = models.OneToOneField(
        Person,
        on_delete=models.CASCADE,
        related_name="academy_teacher",
    )
    title = models.CharField(max_length=200, blank=True)
    bio = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["person__display_name"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        if self.title:
            return f"{self.person.display_name}, {self.title}"

        return self.person.display_name

    def get_absolute_url(self):
        return reverse("academy:teacher-detail", kwargs={"pk": self.pk})


class Course(models.Model):
    title = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="academy_courses",
    )
    owner = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_courses",
    )
    is_published = models.BooleanField(default=False)
    is_virtual = models.BooleanField(
        default=False,
        help_text="Virtual courses are teacher curriculum items and are hidden from the public course list.",
    )
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("academy:course-detail", kwargs={"slug": self.slug})

    @property
    def module_count(self):
        return self.modules.count()

    @property
    def lesson_count(self):
        return Lesson.objects.filter(module__course=self).count()


class CourseModule(models.Model):
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    unlocks_badge = models.ForeignKey(
        SkillBadge,
        on_delete=models.PROTECT,
        related_name="unlocking_modules",
    )
    owner = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_modules",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    description = models.TextField(blank=True)
    verbena_page = models.ForeignKey(
        "palimpsest.Page",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="academy_modules",
        help_text="Optional notes, article, or handout page for this module.",
    )
    exam = models.ForeignKey(
        "quizzes.Quiz",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exam_modules",
        limit_choices_to={"is_official": True},
        help_text="Official quiz used as the exam for this module.",
    )
    attached_quizzes = models.ManyToManyField(
        "quizzes.Quiz",
        related_name="academy_modules",
        blank=True,
        help_text="Standalone quizzes attached to this academy module.",
    )
    lecture_video_url = models.URLField(
        blank=True,
        help_text="Optional VOD link for the lecture video (YouTube, Vimeo, etc.).",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "slug"],
                name="unique_module_slug_per_course",
            ),
            models.UniqueConstraint(
                fields=["course", "unlocks_badge"],
                name="unique_badge_unlock_per_course",
            ),
        ]

    def __str__(self):
        return f"{self.course.title} / {self.title}"


class Lesson(models.Model):
    module = models.ForeignKey(
        CourseModule,
        on_delete=models.CASCADE,
        related_name="lessons",
    )
    owner = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_lessons",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    summary = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    lecture = models.ForeignKey(
        MemoDeck,
        on_delete=models.PROTECT,
        related_name="academy_lessons",
    )
    video_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="academy_lessons",
        help_text="Optional video for this lesson (from Vault).",
    )
    attached_quizzes = models.ManyToManyField(
        "quizzes.Quiz",
        related_name="academy_lessons",
        blank=True,
        help_text="Standalone quizzes attached to this academy lesson.",
    )

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["module", "slug"],
                name="unique_lesson_slug_per_module",
            ),
        ]

    def __str__(self):
        return f"{self.module.title} / {self.title}"


class Student(models.Model):
    person = models.OneToOneField(
        Person,
        on_delete=models.CASCADE,
        related_name="academy_student",
    )

    badges = models.ManyToManyField(
        SkillBadge,
        through="StudentBadge",
        related_name="students",
        blank=True,
    )

    enrolled_courses = models.ManyToManyField(
        Course,
        through="CourseEnrollment",
        related_name="students",
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return str(self.person)

    def has_badge(self, badge):
        return self.badges.filter(pk=badge.pk).exists()


class StudentBadge(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="earned_badges",
    )
    badge = models.ForeignKey(
        SkillBadge,
        on_delete=models.CASCADE,
        related_name="earned_by_students",
    )

    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-awarded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "badge"],
                name="unique_student_badge",
            ),
        ]

    def __str__(self):
        return f"{self.student} earned {self.badge}"


class CourseEnrollment(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="course_enrollments",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="course_enrollments",
    )

    enrolled_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-enrolled_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "course"],
                name="unique_student_course_enrollment",
            ),
        ]

    def __str__(self):
        return f"{self.student} -> {self.course}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.completed_at:
            Certificate.objects.get_or_create(
                person=self.student.person,
                course=self.course,
                defaults={
                    "granted_at": self.completed_at,
                },
            )


class Certificate(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="academy_certificates",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificates",
    )
    exam = models.ForeignKey(
        "quizzes.Quiz",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificates",
        limit_choices_to={"is_official": True},
        help_text="Official exam quiz this certificate was awarded for.",
    )
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    granted_at = models.DateTimeField(default=timezone.now)

    # Cryptographic signature by a teacher (professor).
    signed_by = models.ForeignKey(
        "academy.Teacher",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signed_certificates",
        help_text="Teacher who cryptographically signed this certificate.",
    )
    signing_payload = models.TextField(blank=True)
    cryptographic_signature = models.TextField(blank=True)
    signing_key = models.ForeignKey(
        "gervazy.EncryptedPrivateKey",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="certificate_signatures",
    )

    class Meta:
        ordering = ["-granted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["person", "course"],
                condition=models.Q(course__isnull=False),
                name="unique_course_certificate_per_person",
            ),
        ]

    def __str__(self):
        return self.display_title

    @property
    def display_title(self):
        if self.title:
            return self.title

        if self.course:
            return self.course.title

        return "Certificate"


class Cohort(models.Model):
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="cohorts",
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cohorts",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-starts_at", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "slug"],
                name="unique_cohort_slug_per_course",
            ),
        ]

    def __str__(self):
        return f"{self.course.title} / {self.title}"

    @property
    def member_count(self):
        return self.memberships.count()


class CohortMembership(models.Model):
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="cohort_memberships",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-joined_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["cohort", "student"],
                name="unique_student_cohort_membership",
            ),
        ]

    def __str__(self):
        return f"{self.student} / {self.cohort}"


class LearningPath(models.Model):
    title = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    badges = models.ManyToManyField(
        SkillBadge,
        through="LearningPathBadge",
        related_name="learning_paths",
        blank=True,
    )
    is_published = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("academy:path-detail", kwargs={"slug": self.slug})


class LearningPathBadge(models.Model):
    learning_path = models.ForeignKey(
        LearningPath,
        on_delete=models.CASCADE,
        related_name="badge_steps",
    )
    badge = models.ForeignKey(
        SkillBadge,
        on_delete=models.CASCADE,
        related_name="path_steps",
    )
    order = models.PositiveIntegerField(default=0)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["learning_path", "badge"],
                name="unique_badge_per_path",
            ),
        ]

    def __str__(self):
        return f"{self.learning_path.title} -> {self.badge.title}"


# ────────────────────────────────────────────────
# SCRIPT  (instructional pages per module, multiple allowed)
# ────────────────────────────────────────────────

class Script(AbstractPage):
    """Instructional content page attached to a course module."""
    module = models.ForeignKey(
        CourseModule,
        on_delete=models.CASCADE,
        related_name="scripts",
    )
    author = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="academy_scripts",
    )

    class Meta:
        verbose_name = "Script"
        verbose_name_plural = "Scripts"
        ordering = ["title"]

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("academy:script-detail", args=[self.pk])


class ScriptSection(AbstractSection):
    page = models.ForeignKey(
        Script,
        on_delete=models.CASCADE,
        related_name="sections",
    )

    class Meta:
        ordering = ["order"]
        verbose_name = "Script Section"
        verbose_name_plural = "Script Sections"

    def __str__(self):
        return f"{self.page.title} – {self.title or 'Section'}"
