from django.db import models
from django.utils.text import slugify
from trix_editor.fields import TrixEditorField
from toto.core.domain import DomainEntity
from toto.people.models import Person


# ────────────────────────────────────────────────
# ABSTRACT BASES
# ────────────────────────────────────────────────

class AbstractTag(DomainEntity):
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(unique=True, blank=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class AbstractPage(DomainEntity):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class AbstractSection(DomainEntity):
    title = models.CharField(max_length=255, blank=True)
    content = TrixEditorField(blank=True)
    author = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ["order"]

    def __str__(self):
        return self.title or "Section"
