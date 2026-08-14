"""The quiz desk: list, take, review, certify, and the aggregate picture.

Locked to the global scope exactly as the polls views are — a quiz scoped to
a room or a company belongs to that surface, and its host renders it there.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _

from toto.ui import PageProcessor

from . import downloads, quiz_services, render_pdf
from .models import SCOPE_GLOBAL
from .quiz_models import Quiz, QuizAttempt, QuizCertificate


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _is_operator(user) -> bool:
    return user.is_staff or user.is_superuser


def _global_quiz(slug):
    return get_object_or_404(
        Quiz.objects.in_scope(SCOPE_GLOBAL).prefetch_related(
            "questions__answers"),
        slug=slug)


@login_required
def quiz_list(request):
    quizzes = []
    for quiz in Quiz.objects.in_scope(SCOPE_GLOBAL).active():
        mine = quiz.attempts.filter(user=request.user)
        best = max((a.percent for a in mine), default=None)
        quizzes.append({
            "quiz": quiz,
            "question_count": quiz.questions.count(),
            "attempts_used": mine.count(),
            "attempts_left": quiz.attempts_left(request.user),
            "best_percent": round(best, 1) if best is not None else None,
            "passed_ever": any(a.passed for a in mine),
        })
    return _render(request, "polls/quiz_list.html", {
        "active_tab": "quizzes",
        "rows": quizzes,
        "is_operator": _is_operator(request.user),
    })


@login_required
def quiz_take(request, slug):
    """The sheet. GET renders every question; POST grades the submission."""
    quiz = _global_quiz(slug)

    if request.method == "POST":
        selections = {
            question.pk: request.POST.getlist(f"q{question.pk}")
            for question in quiz.questions.all()
        }
        try:
            attempt = quiz_services.submit_attempt(quiz, request.user,
                                                   selections)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, str(exc) or _("The quiz refused this."))
            return redirect(reverse("polls:quiz_take", args=[quiz.slug]))
        return redirect(reverse("polls:quiz_result",
                                args=[quiz.slug, attempt.number]))

    return _render(request, "polls/quiz_take.html", {
        "active_tab": "quizzes",
        "quiz": quiz,
        "questions": quiz.questions.all(),
        "attempts_left": quiz.attempts_left(request.user),
        "my_attempts": quiz.attempts.filter(user=request.user),
    })


@login_required
def quiz_result(request, slug, number):
    """One of MY attempts. Never anybody else's — results are personal."""
    quiz = _global_quiz(slug)
    attempt = get_object_or_404(QuizAttempt, quiz=quiz, user=request.user,
                                number=number)
    certificate = QuizCertificate.objects.filter(attempt=attempt).first()

    return _render(request, "polls/quiz_result.html", {
        "active_tab": "quizzes",
        "quiz": quiz,
        "attempt": attempt,
        "rows": attempt.answers.select_related("question")
                .prefetch_related("selected"),
        "certificate": certificate,
        "attempts_left": quiz.attempts_left(request.user),
    })


@login_required
def quiz_certificate_pdf(request, slug, number):
    """My paper. Metered like every polls PDF."""
    quiz = _global_quiz(slug)
    attempt = get_object_or_404(QuizAttempt, quiz=quiz, user=request.user,
                                number=number)
    certificate = get_object_or_404(QuizCertificate, attempt=attempt)
    base = slugify(quiz.title) or quiz.slug
    return downloads.metered_pdf(
        request, lambda: render_pdf.certificate_pdf(certificate),
        f"{base}-certificate-{attempt.number}.pdf")


@login_required
def quiz_statistics(request, slug):
    """The aggregate picture — the operator's and the author's view."""
    quiz = _global_quiz(slug)
    if not (_is_operator(request.user) or quiz.created_by_id == request.user.pk):
        raise PermissionDenied(_("The aggregate picture is the author's."))

    return _render(request, "polls/quiz_statistics.html", {
        "active_tab": "quizzes",
        "quiz": quiz,
        "stats": quiz_services.quiz_statistics(quiz),
    })
