"""Taking a quiz and reading the results. The only door into either.

The polls services' argument applies unchanged: the rules that make a test
worth certifying — the attempt allowance, snapshot-at-submission scoring, one
certificate per attempt — are worth nothing if three call sites each
implement them.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .quiz_models import (Quiz, QuizAttempt, QuizAttemptAnswer,
                          QuizCertificate)


class NoAttemptsLeft(PermissionDenied):
    """The allowance is spent. Carries a sentence, like every engine refusal."""


def grade(quiz, selections: dict) -> list:
    """Pure grading: {question_id: [answer_id, ...]} -> per-question results.

    Every question is graded, answered or not — an exam sheet with a blank
    is a wrong answer, not a shorter exam.
    """
    results = []
    for question in quiz.questions.prefetch_related("answers"):
        picked = selections.get(question.pk, [])
        correct = question.check_choice(picked)
        results.append({
            "question": question,
            "selected_ids": [int(i) for i in picked] if picked else [],
            "was_correct": correct,
            "points_earned": question.points if correct else 0,
        })
    return results


@transaction.atomic
def submit_attempt(quiz, user, selections: dict) -> QuizAttempt:
    """Grade one filled sheet and freeze it. Raises with a sentence, or returns.

    No in-progress state: the submission IS the attempt, numbered under lock
    so a double-click cannot spend two tries.
    """
    if not quiz.is_active:
        raise ValidationError(_("This quiz is not open."))
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied(_("Sign in to take a quiz."))

    # Locked so two submissions cannot claim one attempt number — the same
    # select_for_update shape cast() uses on the ballot.
    used = (QuizAttempt.objects.select_for_update()
            .filter(quiz=quiz, user=user).count())
    if quiz.max_attempts is not None and used >= quiz.max_attempts:
        raise NoAttemptsLeft(
            _("You have used all %(n)s attempts.") % {"n": quiz.max_attempts})

    results = grade(quiz, selections)
    score = sum(row["points_earned"] for row in results)
    max_score = sum(row["question"].points for row in results)
    percent = (score / max_score * 100) if max_score else 0.0
    passed = None if quiz.pass_mark is None else (percent >= quiz.pass_mark)

    attempt = QuizAttempt.objects.create(
        quiz=quiz, user=user, number=used + 1,
        score=score, max_score=max_score, percent=percent, passed=passed)
    for row in results:
        answer_row = QuizAttemptAnswer.objects.create(
            attempt=attempt, question=row["question"],
            was_correct=row["was_correct"],
            points_earned=row["points_earned"])
        if row["selected_ids"]:
            answer_row.selected.set(
                row["question"].answers.filter(pk__in=row["selected_ids"]))

    issue_certificate(attempt)
    return attempt


def issue_certificate(attempt) -> QuizCertificate:
    """The result document, from the attempt's frozen numbers.

    Every finished attempt gets one — a certificate of RESULT, so a failed
    attempt's paper honestly says failed. Idempotent per attempt.
    """
    existing = QuizCertificate.objects.filter(attempt=attempt).first()
    if existing is not None:
        return existing

    quiz = attempt.quiz
    if attempt.passed is None:
        status = "completed"
    else:
        status = "passed" if attempt.passed else "failed"

    return QuizCertificate.objects.create(
        attempt=attempt, user=attempt.user,
        quiz_title=quiz.title, percent=attempt.percent,
        passed=attempt.passed,
        content={
            "participant": {"id": attempt.user_id,
                            "username": attempt.user.get_username(),
                            "full_name": (attempt.user.get_full_name() or "")},
            "quiz": {"id": quiz.pk, "title": quiz.title, "slug": quiz.slug,
                     "scope_type": quiz.scope_type,
                     "scope_id": quiz.scope_id,
                     "pass_mark": quiz.pass_mark},
            "score": attempt.score, "max_score": attempt.max_score,
            "percent": round(attempt.percent, 1),
            "status": status,
            "finished_at": attempt.finished_at.isoformat(),
            "attempt": {"number": attempt.number,
                        "allowed": quiz.max_attempts},
            "issued_at": timezone.now().isoformat(),
        })


def quiz_statistics(quiz) -> dict:
    """The aggregate picture: attempts, participants, scores, and where
    people stumble. Fetch once, fold in Python — the kanban lesson — and the
    trailing order_by() on aggregates defeats the Meta-ordering GROUP BY trap.
    """
    attempts = quiz.attempts.all()
    totals = attempts.aggregate(
        n=models.Count("id"),
        participants=models.Count("user", distinct=True),
        avg_percent=models.Avg("percent"))

    passed = attempts.filter(passed=True).count() if quiz.pass_mark is not None \
        else None
    decided = attempts.exclude(passed=None).count()

    per_question = {
        row["question_id"]: (row["correct"], row["total"])
        for row in (QuizAttemptAnswer.objects.filter(attempt__quiz=quiz)
                    .values("question_id")
                    .annotate(correct=models.Sum(models.Case(
                        models.When(was_correct=True, then=1), default=0,
                        output_field=models.IntegerField())),
                              total=models.Count("id"))
                    .order_by())
    }

    questions = []
    for question in quiz.questions.all():
        correct, total = per_question.get(question.pk, (0, 0))
        questions.append({
            "question": question,
            "asked": total,
            "correct": correct,
            "correct_rate": round(correct / total * 100) if total else None,
        })

    return {
        "attempts": totals["n"] or 0,
        "participants": totals["participants"] or 0,
        "avg_percent": round(totals["avg_percent"], 1)
        if totals["avg_percent"] is not None else None,
        "passed": passed,
        "pass_rate": (round(passed / decided * 100)
                      if passed is not None and decided else None),
        "questions": questions,
    }
