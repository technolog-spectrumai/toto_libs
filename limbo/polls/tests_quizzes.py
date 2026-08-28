"""Quizzes: scoring, attempts, pass rules, scope, statistics, certificates."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from . import quiz_services
from .quiz_models import (Quiz, QuizAnswer, QuizAttempt, QuizCertificate,
                          QuizQuestion)

User = get_user_model()


def _platform():
    from toto.core.models import Platform

    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})


def _quiz(**kwargs):
    """Two questions: single-select (2 answers, first correct) and
    multi-select worth 2 points (3 answers, first two correct)."""
    kwargs.setdefault("title", "Safety basics")
    quiz = Quiz.objects.create(**kwargs)
    single = QuizQuestion.objects.create(quiz=quiz, text="Single?",
                                         position=0)
    QuizAnswer.objects.create(question=single, text="Right",
                              is_correct=True, position=0)
    QuizAnswer.objects.create(question=single, text="Wrong", position=1)
    multi = QuizQuestion.objects.create(quiz=quiz, text="Multi?",
                                        is_multiple_choice=True, points=2,
                                        position=1)
    QuizAnswer.objects.create(question=multi, text="A", is_correct=True,
                              position=0)
    QuizAnswer.objects.create(question=multi, text="B", is_correct=True,
                              position=1)
    QuizAnswer.objects.create(question=multi, text="C", position=2)
    return quiz


def _answers(quiz):
    single, multi = quiz.questions.all()
    return {
        "single": single,
        "multi": multi,
        "right": single.answers.get(text="Right"),
        "wrong": single.answers.get(text="Wrong"),
        "a": multi.answers.get(text="A"),
        "b": multi.answers.get(text="B"),
        "c": multi.answers.get(text="C"),
    }


class ScoringTests(TestCase):
    """Delta's checking rule, verbatim — the mechanic the extraction owed."""

    def setUp(self):
        self.user = User.objects.create_user("taker", password="pw")
        self.quiz = _quiz()
        self.parts = _answers(self.quiz)

    def _submit(self, single_pick=None, multi_picks=None):
        selections = {}
        if single_pick is not None:
            selections[self.parts["single"].pk] = [single_pick.pk]
        if multi_picks is not None:
            selections[self.parts["multi"].pk] = [a.pk for a in multi_picks]
        return quiz_services.submit_attempt(self.quiz, self.user, selections)

    def test_a_perfect_sheet_scores_full_points(self):
        attempt = self._submit(self.parts["right"],
                               [self.parts["a"], self.parts["b"]])

        self.assertEqual((attempt.score, attempt.max_score), (3, 3))
        self.assertEqual(attempt.percent, 100.0)

    def test_multi_select_demands_the_exact_set(self):
        """A shotgun of every option scores nothing — delta's rule."""
        attempt = self._submit(self.parts["right"],
                               [self.parts["a"], self.parts["b"],
                                self.parts["c"]])

        self.assertEqual(attempt.score, 1)

    def test_a_partial_multi_selection_scores_nothing(self):
        attempt = self._submit(self.parts["right"], [self.parts["a"]])

        self.assertEqual(attempt.score, 1)

    def test_a_blank_question_is_a_wrong_answer_not_a_shorter_exam(self):
        attempt = self._submit(self.parts["right"], None)

        self.assertEqual(attempt.score, 1)
        self.assertEqual(attempt.max_score, 3)

    def test_correctness_is_snapshotted_against_later_edits(self):
        """Editing the quiz afterwards must not rewrite what was scored."""
        attempt = self._submit(self.parts["right"],
                               [self.parts["a"], self.parts["b"]])

        self.parts["right"].is_correct = False
        self.parts["right"].save()

        attempt.refresh_from_db()
        self.assertEqual(attempt.score, 3)
        row = attempt.answers.get(question=self.parts["single"])
        self.assertTrue(row.was_correct)


class AttemptTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("taker", password="pw")

    def test_attempts_number_up_and_run_out(self):
        quiz = _quiz(max_attempts=2)
        parts = _answers(quiz)

        first = quiz_services.submit_attempt(quiz, self.user, {})
        second = quiz_services.submit_attempt(quiz, self.user, {})

        self.assertEqual((first.number, second.number), (1, 2))
        with self.assertRaises(quiz_services.NoAttemptsLeft):
            quiz_services.submit_attempt(quiz, self.user, {})
        self.assertEqual(quiz.attempts_left(self.user), 0)

    def test_unlimited_attempts_when_no_cap(self):
        quiz = _quiz()

        for _i in range(3):
            quiz_services.submit_attempt(quiz, self.user, {})

        self.assertIsNone(quiz.attempts_left(self.user))
        self.assertEqual(quiz.attempts.filter(user=self.user).count(), 3)

    def test_an_inactive_quiz_refuses(self):
        quiz = _quiz(is_active=False)

        with self.assertRaises(ValidationError):
            quiz_services.submit_attempt(quiz, self.user, {})

    def test_each_participant_gets_their_own_allowance(self):
        quiz = _quiz(max_attempts=1)
        other = User.objects.create_user("other", password="pw")

        quiz_services.submit_attempt(quiz, self.user, {})
        attempt = quiz_services.submit_attempt(quiz, other, {})

        self.assertEqual(attempt.number, 1)


class PassRuleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("taker", password="pw")

    def _take(self, quiz, *, perfect):
        parts = _answers(quiz)
        selections = {parts["single"].pk: [parts["right"].pk]}
        if perfect:
            selections[parts["multi"].pk] = [parts["a"].pk, parts["b"].pk]
        return quiz_services.submit_attempt(quiz, self.user, selections)

    def test_the_pass_mark_is_met_or_not(self):
        quiz = _quiz(pass_mark=60)

        self.assertTrue(self._take(quiz, perfect=True).passed)

    def test_below_the_mark_fails(self):
        quiz = _quiz(pass_mark=60)

        # 1 of 3 points = 33%
        self.assertFalse(self._take(quiz, perfect=False).passed)

    def test_the_mark_is_inclusive(self):
        """Scoring exactly the mark passes — a mark is a bar, not a wall."""
        quiz = _quiz(pass_mark=100)

        self.assertTrue(self._take(quiz, perfect=True).passed)

    def test_completion_only_judges_nobody(self):
        quiz = _quiz()  # no pass_mark

        attempt = self._take(quiz, perfect=False)

        self.assertIsNone(attempt.passed)


class ScopeIsolationTests(TestCase):
    def setUp(self):
        _platform()
        self.user = User.objects.create_user("taker", password="pw")
        self.client.force_login(self.user)

    def test_a_scoped_quiz_never_lists_globally(self):
        _quiz(title="Global quiz")
        _quiz(title="Room secret", scope_type="forum.channel", scope_id="7")

        response = self.client.get(reverse("polls:quiz_list"))

        self.assertContains(response, "Global quiz")
        self.assertNotContains(response, "Room secret")

    def test_a_scoped_quiz_is_unreachable_by_slug_on_the_global_pages(self):
        scoped = _quiz(title="Room secret", scope_type="forum.channel",
                       scope_id="7")

        response = self.client.get(
            reverse("polls:quiz_take", args=[scoped.slug]))

        self.assertEqual(response.status_code, 404)

    def test_two_scopes_may_reuse_a_slug(self):
        first = _quiz(title="Basics", scope_type="forum.channel",
                      scope_id="1")
        second = _quiz(title="Basics", scope_type="forum.channel",
                       scope_id="2")

        self.assertEqual(first.slug, second.slug)


class StatisticsTests(TestCase):
    def setUp(self):
        self.quiz = _quiz(pass_mark=60)
        self.parts = _answers(self.quiz)

    def _take(self, username, *, perfect):
        user = User.objects.create_user(username, password="pw")
        selections = {self.parts["single"].pk: [self.parts["right"].pk]}
        if perfect:
            selections[self.parts["multi"].pk] = [self.parts["a"].pk,
                                                  self.parts["b"].pk]
        return quiz_services.submit_attempt(self.quiz, user, selections)

    def test_the_aggregate_picture_adds_up(self):
        self._take("u1", perfect=True)    # 100, passed
        self._take("u2", perfect=False)   # 33, failed

        stats = quiz_services.quiz_statistics(self.quiz)

        self.assertEqual(stats["attempts"], 2)
        self.assertEqual(stats["participants"], 2)
        self.assertEqual(stats["passed"], 1)
        self.assertEqual(stats["pass_rate"], 50)

    def test_per_question_difficulty(self):
        self._take("u1", perfect=True)
        self._take("u2", perfect=False)

        stats = quiz_services.quiz_statistics(self.quiz)
        by_text = {row["question"].text: row for row in stats["questions"]}

        self.assertEqual(by_text["Single?"]["correct_rate"], 100)
        self.assertEqual(by_text["Multi?"]["correct_rate"], 50)

    def test_the_statistics_page_is_the_authors_and_staffs(self):
        _platform()
        civilian = User.objects.create_user("civ", password="pw")
        staff = User.objects.create_user("op", password="pw", is_staff=True)
        url = reverse("polls:quiz_statistics", args=[self.quiz.slug])

        self.client.force_login(civilian)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code, 200)
