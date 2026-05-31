from django.db import models
from django.urls import reverse
from django.utils.html import strip_tags

from toto.people.models import Person
from toto.verbena.models import AbstractPage, AbstractSection, AbstractTag
from toto.verbena.utils import unique_slug


class Tag(AbstractTag):
    class Meta:
        verbose_name = "Blog tag"
        verbose_name_plural = "Blog tags"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name)
        super().save(*args, **kwargs)


class Page(AbstractPage):
    tags = models.ManyToManyField(Tag, related_name="pages", blank=True)
    is_private = models.BooleanField(
        default=False,
        help_text="Private pages are only visible to logged-in users.",
    )

    class Meta:
        verbose_name = "Blog post"
        verbose_name_plural = "Blog posts"
        ordering = ["-created_at"]

    def authors(self):
        return Person.objects.filter(palimpsest_sections__page=self).distinct()

    @property
    def author_names(self):
        names = []
        seen = set()
        for section in self.sections.all():
            if not section.author_id or section.author_id in seen:
                continue
            seen.add(section.author_id)
            names.append(section.author.display_name)
        return ", ".join(names) if names else "Blog circle"

    @property
    def section_count(self):
        return len(self.sections.all())

    @property
    def word_count(self):
        words = []
        if self.description:
            words.extend(strip_tags(self.description).split())
        for section in self.sections.all():
            words.extend(strip_tags(section.content or "").split())
        return len(words)

    @property
    def reading_time_minutes(self):
        return max(1, round(self.word_count / 220))

    @property
    def excerpt(self):
        if self.description:
            return self.description
        first_section = next(iter(self.sections.all()), None)
        if not first_section:
            return ""
        plain_text = " ".join(strip_tags(first_section.content or "").split())
        if len(plain_text) <= 220:
            return plain_text
        return f"{plain_text[:217].rstrip()}..."

    def get_absolute_url(self):
        return reverse("palimpsest:page_detail", args=[self.slug])

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.title)
        super().save(*args, **kwargs)


class Section(AbstractSection):
    page = models.ForeignKey(Page, related_name="sections", on_delete=models.CASCADE)
    author = models.ForeignKey(
        Person,
        related_name="palimpsest_sections",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    tags = models.ManyToManyField(Tag, related_name="sections", blank=True)

    def __str__(self):
        return f"{self.page.title} - {self.title or 'Section'}"
