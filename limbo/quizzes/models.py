from django.db import models

from toto.people.models import Person


class Quiz(models.Model):
    owner = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_quizzes",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    description = models.TextField(blank=True)
    is_published = models.BooleanField(default=False)
    is_official = models.BooleanField(
        default=False,
        help_text="Official quizzes can be set as the exam for a course module.",
    )
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title


class QuizQuestion(models.Model):
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    text = models.TextField()
    explanation = models.TextField(blank=True)
    is_multiple_choice = models.BooleanField(
        default=False,
        help_text="If true, the student may select multiple answers.",
    )
    max_time = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional time limit for this question in minutes.",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text[:80]


class QuizAnswer(models.Model):
    question = models.ForeignKey(
        QuizQuestion,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    text = models.TextField()
    is_correct = models.BooleanField(default=False)
    explanation = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    traits = models.ManyToManyField(
        "QuizTrait",
        through="QuizAnswerTrait",
        related_name="quiz_answers",
        blank=True,
    )

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text[:80]


class QuizAnswerTrait(models.Model):
    answer = models.ForeignKey(
        QuizAnswer,
        on_delete=models.CASCADE,
        related_name="trait_mappings",
    )
    trait = models.ForeignKey(
        "QuizTrait",
        on_delete=models.CASCADE,
        related_name="answer_mappings",
    )
    weight = models.IntegerField(
        default=1,
        help_text="How strongly this answer contributes to the trait.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["answer", "trait"],
                name="unique_trait_per_quiz_answer",
            ),
        ]

    def __str__(self):
        return f"{self.answer} -> {self.trait} ({self.weight})"


class QuizTrait(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=100, default="fa-solid fa-star")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title


class QuizAttempt(models.Model):
    participant = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="quiz_attempts",
    )
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.participant} -> {self.quiz}"

    def trait_scores(self):
        scores = {}
        mappings = (
            QuizAnswerTrait.objects
            .filter(answer__attempt_selections__attempt=self)
            .select_related("trait")
        )
        for mapping in mappings:
            scores[mapping.trait] = scores.get(mapping.trait, 0) + mapping.weight
        return scores


class QuizAttemptAnswer(models.Model):
    attempt = models.ForeignKey(
        QuizAttempt,
        on_delete=models.CASCADE,
        related_name="selected_answers",
    )
    question = models.ForeignKey(
        QuizQuestion,
        on_delete=models.CASCADE,
        related_name="attempt_answers",
    )
    answer = models.ForeignKey(
        QuizAnswer,
        on_delete=models.CASCADE,
        related_name="attempt_selections",
    )
    selected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "question", "answer"],
                name="unique_selected_answer_per_attempt",
            ),
        ]

    def __str__(self):
        return f"{self.attempt} / {self.question} -> {self.answer}"
