from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
import datetime

User = get_user_model()


class Question(models.Model):
    name = models.CharField(max_length=100, unique=True)
    question_text = models.CharField(max_length=200)
    pub_date = models.DateTimeField("date published")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    def was_published_recently(self):
        return self.pub_date >= timezone.now() - datetime.timedelta(days=1)


class Choice(models.Model):
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="choices"
    )
    label = models.CharField(max_length=50)
    choice_text = models.CharField(max_length=200)
    value = models.IntegerField(default=0)  # ← NEW FIELD

    # class Meta:
    #     constraints = [
    #         models.UniqueConstraint(
    #             fields=["question", "label"],
    #             name="unique_label_per_question"
    #         )
    #     ]

    def __str__(self):
        return f"{self.label}: {self.choice_text} (value={self.value})"



class Answer(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="answers"
    )
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="answers"
    )
    choice = models.ForeignKey(
        Choice,
        on_delete=models.CASCADE,
        related_name="answers"
    )
    answered_at = models.DateTimeField(auto_now_add=True)

    # class Meta:
    #     constraints = [
    #         models.UniqueConstraint(
    #             fields=["user", "question"],
    #             name="unique_answer_per_user_per_question"
    #         ),
    #         models.CheckConstraint(
    #             check=models.Q(choice__question=models.F("question")),
    #             name="choice_must_belong_to_question"
    #         )
    #     ]

    def __str__(self):
        return f"{self.user} → {self.question} = {self.choice}"
