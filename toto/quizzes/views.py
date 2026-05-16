import json

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView

from toto.ui import PageProcessor

from .models import Quiz, QuizAnswer, QuizAttempt, QuizAttemptAnswer


class QuizListView(ListView):
    model = Quiz
    template_name = "quizzes/quiz_list.html"
    context_object_name = "quizzes"

    def get_queryset(self):
        queryset = (
            Quiz.objects
            .select_related("owner")
            .prefetch_related("questions")
        )
        if not self.request.user.is_staff:
            queryset = queryset.filter(is_published=True)
        return queryset.order_by("order", "title")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


def _get_participant(request):
    return getattr(request.user, "community_profile", None)


@login_required
def quiz_detail(request, pk):
    quiz = get_object_or_404(
        Quiz.objects.prefetch_related(
            "questions",
            "questions__answers",
            "questions__answers__traits",
        ),
        pk=pk,
        is_published=True,
    )

    participant = _get_participant(request)
    existing_attempt = None

    if participant:
        existing_attempt = (
            QuizAttempt.objects
            .filter(participant=participant, quiz=quiz, completed_at__isnull=False)
            .prefetch_related(
                "selected_answers__question",
                "selected_answers__answer",
            )
            .first()
        )

    if request.method == "POST":
        if existing_attempt:
            return redirect("quizzes:quiz-detail", pk=quiz.pk)

        if not participant:
            return redirect("quizzes:quiz-detail", pk=quiz.pk)

        attempt = QuizAttempt.objects.create(participant=participant, quiz=quiz)
        questions = list(quiz.questions.all())

        for question in questions:
            if question.is_multiple_choice:
                answer_ids = request.POST.getlist(f"question_{question.pk}")
            else:
                raw = request.POST.get(f"question_{question.pk}")
                answer_ids = [raw] if raw else []

            for answer_id in answer_ids:
                try:
                    answer = question.answers.get(pk=answer_id)
                    QuizAttemptAnswer.objects.get_or_create(
                        attempt=attempt,
                        question=question,
                        answer=answer,
                    )
                except QuizAnswer.DoesNotExist:
                    pass

        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["completed_at"])

        return redirect("quizzes:quiz-detail", pk=quiz.pk)

    selected_answer_ids = set()
    if existing_attempt:
        selected_answer_ids = {
            aa.answer_id for aa in existing_attempt.selected_answers.all()
        }

    questions_data = []
    for question in quiz.questions.all():
        questions_data.append({
            "question": question,
            "answers": list(question.answers.all()),
            "selected_ids": selected_answer_ids,
        })

    back_url = reverse("quizzes:quiz-list")
    back_label = "Quizzes"

    context = {
        "quiz": quiz,
        "questions_data": questions_data,
        "existing_attempt": existing_attempt,
        "back_url": back_url,
        "back_label": back_label,
        "can_view_metrics": request.user.is_staff or (participant and quiz.owner_id == participant.pk),
    }
    return render(request, "quizzes/quiz_detail.html", PageProcessor().decorate(context, request))


@login_required
def quiz_metrics(request, pk):
    quiz = get_object_or_404(
        Quiz.objects.prefetch_related(
            "questions",
            "questions__answers",
            "questions__answers__traits",
            "attempts",
            "attempts__selected_answers",
        ),
        pk=pk,
    )

    person = getattr(request.user, "community_profile", None)
    is_owner = person and quiz.owner_id == person.pk
    if not (request.user.is_staff or is_owner):
        raise Http404

    total_attempts = quiz.attempts.count()
    completed_attempts = quiz.attempts.filter(completed_at__isnull=False).count()

    questions_stats = []
    for question in quiz.questions.all():
        answer_stats = []
        total_selections = 0

        for answer in question.answers.all():
            count = QuizAttemptAnswer.objects.filter(
                attempt__quiz=quiz,
                attempt__completed_at__isnull=False,
                question=question,
                answer=answer,
            ).count()
            total_selections += count
            answer_stats.append({"answer": answer, "count": count})

        for stat in answer_stats:
            stat["pct"] = round((stat["count"] / total_selections * 100) if total_selections else 0)

        chart = {
            "chart_type": "bar",
            "labels": [s["answer"].text[:40] for s in answer_stats],
            "datasets": [{
                "label": "Selections",
                "data": [s["count"] for s in answer_stats],
                "backgroundColor": ["#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#3B82F6", "#8B5CF6"],
            }],
            "options": {"scales": {"y": {"beginAtZero": True}}},
        }

        questions_stats.append({
            "question": question,
            "answer_stats": answer_stats,
            "total_selections": total_selections,
            "chart_json": json.dumps(chart),
        })

    trait_totals = {}
    for attempt in quiz.attempts.filter(completed_at__isnull=False):
        for trait, score in attempt.trait_scores().items():
            trait_totals[trait] = trait_totals.get(trait, 0) + score

    trait_chart = None
    if trait_totals:
        sorted_traits = sorted(trait_totals.items(), key=lambda x: -x[1])
        trait_chart = json.dumps({
            "chart_type": "bar",
            "labels": [trait.title for trait, _ in sorted_traits],
            "datasets": [{
                "label": "Total weight",
                "data": [score for _, score in sorted_traits],
                "backgroundColor": ["#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#3B82F6", "#8B5CF6"],
            }],
            "options": {"scales": {"y": {"beginAtZero": True}}},
        })

    back_url = reverse("quizzes:quiz-list")
    back_label = "Quizzes"

    context = {
        "quiz": quiz,
        "total_attempts": total_attempts,
        "completed_attempts": completed_attempts,
        "questions_stats": questions_stats,
        "trait_chart_json": trait_chart,
        "back_url": back_url,
        "back_label": back_label,
    }
    return render(request, "quizzes/quiz_metrics.html", PageProcessor().decorate(context, request))
