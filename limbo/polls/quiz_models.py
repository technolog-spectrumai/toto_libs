"""Quizzes: competence testing on the polls app's scoping.

Extracted from delta's e-learning quizzes — the MECHANICS, not the pedagogy.
What came along: multiple-choice questions with per-answer correctness, the
single/multi-select checking rule (verbatim — it is the tested behaviour),
and the attempt/selection shape. What deliberately stayed behind in delta:
open typed answers, hints, worked solutions, psychometric traits and the
practice pool — those are e-learning, and this is an exam desk. Delta keeps
its own app; if it later adopts this one, that is a vendor pull and a data
migration, not a promise this module makes.

Scope works exactly as it does for a Question: a ``(scope_type, scope_id)``
soft pair with ``in_scope()`` as the only listing door, so a host can later
hang quizzes off a room or a company the way it hangs votes.

History cannot be rewritten: an attempt snapshots each selection's
correctness and points AT SUBMISSION, so editing a quiz afterwards changes
future attempts only. A certificate is written once, from those snapshots,
and refuses edits the way a Decision does.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


class QuizQuerySet(models.QuerySet):
    def in_scope(self, scope_type: str = "", scope_id: str = ""):
        """Quizzes belonging to exactly one scope — the ONLY door."""
        return self.filter(scope_type=scope_type, scope_id=str(scope_id or ""))

    def active(self):
        return self.filter(is_active=True)


class Quiz(models.Model):
    """One test: questions, a pass rule, an attempt allowance."""

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, blank=True)
    description = models.TextField(blank=True)

    scope_type = models.CharField(max_length=40, blank=True, db_index=True)
    scope_id = models.CharField(max_length=64, blank=True, db_index=True)

    #: Percent, 0–100. Null means there is no pass/fail judgment — finishing
    #: IS the achievement, and the certificate says "completed".
    pass_mark = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text=_("Score percent needed to pass. Empty: completion-only — "
                    "no pass/fail, finishing is what counts."))
    #: Null = unlimited.
    max_attempts = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text=_("How many submissions each participant gets. "
                    "Empty: unlimited."))
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   blank=True, on_delete=models.SET_NULL,
                                   related_name="quizzes_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = QuizQuerySet.as_manager()

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(fields=["scope_type", "scope_id", "slug"],
                                    name="uniq_quiz_slug_per_scope"),
        ]
        indexes = [
            models.Index(fields=["scope_type", "scope_id", "is_active"]),
        ]
        verbose_name = _("quiz")
        verbose_name_plural = _("quizzes")

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.title) or "quiz"
        slug, counter = base, 1
        siblings = Quiz.objects.in_scope(self.scope_type, self.scope_id)
        while siblings.filter(slug=slug).exclude(pk=self.pk).exists():
            counter += 1
            slug = f"{base}-{counter}"
        return slug

    def max_score(self) -> int:
        return sum(question.points for question in self.questions.all())

    def attempts_left(self, user) -> int | None:
        """None = unlimited; otherwise how many submissions remain."""
        if self.max_attempts is None:
            return None
        used = self.attempts.filter(user=user).count()
        return max(0, self.max_attempts - used)


class QuizQuestion(models.Model):
    """Multiple choice, single- or multi-select. The one kind this desk has."""

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE,
                             related_name="questions")
    text = models.TextField()
    #: Several answers may be picked; scoring then demands the EXACT correct
    #: set. Delta's rule, verbatim.
    is_multiple_choice = models.BooleanField(default=False)
    points = models.PositiveSmallIntegerField(default=1)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.text[:80]

    def correct_answer_ids(self) -> set:
        return set(self.answers.filter(is_correct=True)
                   .values_list("id", flat=True))

    def check_choice(self, selected_ids) -> bool:
        """Delta's checking rule, unchanged — it is the tested mechanic.

        Multi-select: the selection must equal the correct set exactly (a
        shotgun of every option scores nothing). Single-select: exactly one
        answer picked, and it is a correct one.
        """
        try:
            selected = {int(i) for i in selected_ids}
        except (TypeError, ValueError):
            return False
        correct = self.correct_answer_ids()
        if not correct:
            return False
        if self.is_multiple_choice:
            return selected == correct
        return len(selected) == 1 and selected.issubset(correct)


class QuizAnswer(models.Model):
    question = models.ForeignKey(QuizQuestion, on_delete=models.CASCADE,
                                 related_name="answers")
    text = models.TextField()
    is_correct = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.text[:80]


class QuizAttempt(models.Model):
    """One submission: created and scored in the same transaction.

    There is no in-progress state — a filled form arrives, is graded against
    the quiz AS IT IS at that moment, and the numbers freeze. ``passed`` is
    null for completion-only quizzes: "did I pass" is a question their rule
    deliberately does not answer.
    """

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE,
                             related_name="attempts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name="quiz_attempts")
    number = models.PositiveSmallIntegerField()
    score = models.PositiveIntegerField(default=0)
    max_score = models.PositiveIntegerField(default=0)
    percent = models.FloatField(default=0.0)
    passed = models.BooleanField(null=True, blank=True)
    finished_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-finished_at"]
        constraints = [
            models.UniqueConstraint(fields=["quiz", "user", "number"],
                                    name="uniq_quiz_attempt_number"),
        ]
        indexes = [
            models.Index(fields=["quiz", "user"]),
        ]

    def __str__(self):
        return f"{self.user} → {self.quiz} #{self.number}"


class QuizAttemptAnswer(models.Model):
    """One question's outcome inside one attempt — snapshotted.

    ``was_correct`` and ``points_earned`` are copied in at grading time, so a
    later edit to the quiz cannot rewrite what this attempt scored. The same
    reasoning as Ballot.weight, and the campaign's recurring theme.
    """

    attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE,
                                related_name="answers")
    question = models.ForeignKey(QuizQuestion, on_delete=models.CASCADE,
                                 related_name="attempt_answers")
    selected = models.ManyToManyField(QuizAnswer, blank=True,
                                      related_name="selections")
    was_correct = models.BooleanField(default=False)
    points_earned = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["attempt", "question"],
                                    name="uniq_attempt_question"),
        ]

    def __str__(self):
        return f"{self.attempt} / {self.question_id}"


class QuizCertificate(models.Model):
    """The paper trail of one finished attempt. Written once, never edited.

    A result document, not a trophy: a failed attempt gets one too, saying
    "failed" — pass/fail or completion status is part of the record. The PDF
    renders from ``content``, never from live rows, so the certificate keeps
    saying what was true when it was issued.
    """

    attempt = models.OneToOneField(QuizAttempt, on_delete=models.PROTECT,
                                   related_name="certificate")
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name="quiz_certificates")
    serial = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    quiz_title = models.CharField(max_length=200)
    percent = models.FloatField()
    passed = models.BooleanField(null=True, blank=True)
    issued_at = models.DateTimeField(default=timezone.now)
    content = models.JSONField(default=dict)

    class Meta:
        ordering = ["-issued_at"]
        verbose_name = _("quiz certificate")
        verbose_name_plural = _("quiz certificates")

    def __str__(self):
        return f"{self.quiz_title}: {self.user} ({self.serial})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("A certificate is never edited. Reissue by a "
                             "new attempt instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("A certificate is never deleted; it was issued.")
