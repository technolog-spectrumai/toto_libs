from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from toto.core.domain import DomainEntity
from toto.socialhub.models import Person   # ← all authors now use Person


# ────────────────────────────────────────────────
# TAG
# ────────────────────────────────────────────────

class Tag(DomainEntity):
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


# ────────────────────────────────────────────────
# PAGE
# ────────────────────────────────────────────────

class Page(DomainEntity):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(blank=True)

    tags = models.ManyToManyField(Tag, related_name="pages", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def authors(self):
        return Person.objects.filter(
            verbena_sections__page=self
        ).distinct()

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("verbena:page_detail", args=[self.slug])


# ────────────────────────────────────────────────
# IMAGE (NOT projected)
# ────────────────────────────────────────────────

class Image(models.Model):
    title = models.CharField(max_length=255, blank=True)
    file = models.ImageField(upload_to="verbena_images/")

    def __str__(self):
        return self.title or f"Image {self.id}"


# ────────────────────────────────────────────────
# TOPIC
# ────────────────────────────────────────────────

class Topic(DomainEntity):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(blank=True)

    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="topics"
    )

    person = models.ForeignKey(
        "socialhub.Person",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="topics"
    )

    event = models.ForeignKey(
        "events.Event",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="topics"
    )

    route = models.ForeignKey(
        "locations.Route",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="topics"
    )

    territory = models.ForeignKey(
        "locations.Territory",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="topics"
    )

    address = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="topics"
    )

    federation = models.ForeignKey(
        "core.Federation",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="topics"
    )

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


# ────────────────────────────────────────────────
# SECTION
# ────────────────────────────────────────────────

class Section(DomainEntity):
    page = models.ForeignKey(Page, related_name="sections", on_delete=models.CASCADE)

    title = models.CharField(max_length=255, blank=True)
    content = models.TextField(blank=True)

    author = models.ForeignKey(
        Person,
        related_name="verbena_sections",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    order = models.PositiveIntegerField(default=0)

    tags = models.ManyToManyField(Tag, related_name="sections", blank=True)
    topics = models.ManyToManyField(Topic, related_name="sections", blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.page.title} – {self.title or 'Section'}"


# ────────────────────────────────────────────────
# SUBSECTION
# ────────────────────────────────────────────────

class Subsection(DomainEntity):
    section = models.ForeignKey(Section, related_name="subsections", on_delete=models.CASCADE)

    title = models.CharField(max_length=255, blank=True)
    content = models.TextField(blank=True)

    image = models.ForeignKey(
        Image,
        related_name="subsections",
        on_delete=models.SET_NULL,
        null=True, blank=True
    )

    order = models.PositiveIntegerField(default=0)
    topics = models.ManyToManyField(Topic, related_name="subsections", blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.section.title} – {self.title or 'Subsection'}"


# ────────────────────────────────────────────────
# BOOK
# ────────────────────────────────────────────────

class Book(DomainEntity):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(blank=True)

    cover_image = models.ForeignKey(
        Image,
        related_name="book_covers",
        on_delete=models.SET_NULL,
        null=True, blank=True
    )

    tags = models.ManyToManyField(Tag, related_name="books", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def pages(self):
        return Page.objects.filter(chapters__book=self).order_by("chapters__order")

    def authors(self):
        return Person.objects.filter(
            verbena_sections__page__chapters__book=self
        ).distinct()

    def __str__(self):
        return self.title


# ────────────────────────────────────────────────
# CHAPTER
# ────────────────────────────────────────────────

class Chapter(DomainEntity):
    book = models.ForeignKey(Book, related_name="chapters", on_delete=models.CASCADE)
    page = models.ForeignKey(Page, related_name="books", on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        unique_together = ("book", "page")

    def __str__(self):
        return f"{self.book.title} → {self.page.title} (#{self.order})"
