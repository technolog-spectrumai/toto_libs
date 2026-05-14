from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
import datetime

from django.utils.text import slugify

User = get_user_model()


class Poll(models.Model):
    name = models.CharField(max_length=100, unique=True)
    question_text = models.CharField(max_length=200)
    pub_date = models.DateTimeField("date published", default=timezone.now)
    is_active = models.BooleanField(default=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)

    def __str__(self):
        return self.name

    def was_published_recently(self):
        return self.pub_date >= timezone.now() - datetime.timedelta(days=1)

    def save(self, *args, **kwargs):
        # Auto-generate slug if missing
        if not self.slug:
            base = slugify(self.name)
            slug = base
            counter = 1

            # Ensure uniqueness
            while Poll.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                counter += 1
                slug = f"{base}-{counter}"

            self.slug = slug

        super().save(*args, **kwargs)


class Option(models.Model):
    poll = models.ForeignKey(
        Poll,
        on_delete=models.CASCADE,
        related_name="options"
    )
    label = models.CharField(max_length=50)
    option_text = models.CharField(max_length=200)

    # Optional: numeric value (for scoring, weighted voting, etc.)
    value = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["poll", "label"],
                name="unique_label_per_poll"
            )
        ]

    def __str__(self):
        return f"{self.label}: {self.option_text} (value={self.value})"


class Vote(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="votes"
    )
    poll = models.ForeignKey(
        Poll,
        on_delete=models.CASCADE,
        related_name="votes"
    )
    option = models.ForeignKey(
        Option,
        on_delete=models.CASCADE,
        related_name="votes"
    )
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Prevent a user from voting twice on the same poll
            models.UniqueConstraint(
                fields=["user", "poll"],
                name="unique_vote_per_user_per_poll"
            )
        ]

    def __str__(self):
        return f"{self.user} → {self.poll.name} = {self.option.label}"
