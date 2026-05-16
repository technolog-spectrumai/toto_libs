from django.db import models

from toto.people.models import Person


class Experience(models.Model):
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="experiences",
    )
    title = models.CharField(max_length=200)
    institution = models.CharField(max_length=200, blank=True)
    place = models.CharField(max_length=200, blank=True)
    started_at = models.DateField(null=True, blank=True)
    ended_at = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "-started_at", "title"]

    def __str__(self):
        if self.institution:
            return f"{self.title} at {self.institution}"
        return self.title


class SkillGroup(models.Model):
    title = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title


class SkillBadge(models.Model):
    group = models.ForeignKey(
        SkillGroup,
        on_delete=models.CASCADE,
        related_name="badges",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=100, default="fa-solid fa-award")
    order = models.PositiveIntegerField(default=0)
    prerequisites = models.ManyToManyField(
        "self",
        through="SkillBadgePrerequisite",
        symmetrical=False,
        related_name="unlocks",
        blank=True,
    )

    class Meta:
        ordering = ["group__order", "order", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["group", "slug"],
                name="unique_badge_slug_per_group",
            ),
        ]

    def __str__(self):
        return f"{self.group.title} / {self.title}"


class SkillBadgePrerequisite(models.Model):
    badge = models.ForeignKey(
        SkillBadge,
        on_delete=models.CASCADE,
        related_name="prerequisite_links",
    )
    prerequisite = models.ForeignKey(
        SkillBadge,
        on_delete=models.CASCADE,
        related_name="required_for_links",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["badge", "prerequisite"],
                name="unique_badge_prerequisite",
            ),
            models.CheckConstraint(
                check=~models.Q(badge=models.F("prerequisite")),
                name="badge_cannot_require_itself",
            ),
        ]

    def __str__(self):
        return f"{self.prerequisite.title} -> {self.badge.title}"
