from django.db import models


class Experience(models.Model):
    person = models.ForeignKey(
        "socialhub.Person",
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
