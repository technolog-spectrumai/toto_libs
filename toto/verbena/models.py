from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from toto.core.domain import DomainEntity
from toto.socialhub.models import Person  # all authors use Person


# ────────────────────────────────────────────────
# TAG (shared across pages, sections, and library)
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

    # ─────────── Bibliography ───────────
    # ─────────── Bibliography fields ───────────
    books_refs = models.ManyToManyField(
        "verbena.Book", blank=True, related_name="pages_refs"
    )
    articles_refs = models.ManyToManyField(
        "verbena.Article", blank=True, related_name="pages_refs"
    )
    audios_refs = models.ManyToManyField(
        "verbena.Audio", blank=True, related_name="pages_refs"
    )
    videos_refs = models.ManyToManyField(
        "verbena.Video", blank=True, related_name="pages_refs"
    )

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def authors(self):
        return Person.objects.filter(
            verbena_sections__page=self
        ).distinct()

    def get_references(self):
        """
        Returns all bibliographic references attached to this page, sorted by year descending.
        """
        refs = list(self.books_refs.all()) + list(self.articles_refs.all()) + \
               list(self.audios_refs.all()) + list(self.videos_refs.all())
        return sorted(refs, key=lambda r: r.year or 0, reverse=True)

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
# IMAGE
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
# LIBRARY MODELS
# ────────────────────────────────────────────────

class Reference(DomainEntity):
    """
    Abstract base model for all bibliographic references (Books, Articles, Audio, Video)
    """
    title = models.CharField(max_length=500)
    authors = models.ManyToManyField(Person, related_name="%(class)s_references", blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    doi = models.CharField(max_length=255, blank=True)
    url = models.URLField(blank=True)
    abstract = models.TextField(blank=True)
    tags = models.ManyToManyField(Tag, related_name="%(class)s_references", blank=True)

    slug = models.SlugField(unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["-year", "title"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    def get_bibtex_key(self):
        first_author = self.authors.first().last_name if self.authors.exists() else "anon"
        return f"{first_author}{self.year or 'n.d.'}{slugify(self.title)[:20]}"

    @property
    def bibtex_type(self):
        return "misc"


class Book(Reference):
    publisher = models.CharField(max_length=255, blank=True)
    edition = models.CharField(max_length=50, blank=True)
    isbn = models.CharField(max_length=20, blank=True)

    @property
    def bibtex_type(self):
        return "book"


class Article(Reference):
    journal = models.CharField(max_length=255, blank=True)
    volume = models.CharField(max_length=20, blank=True)
    issue = models.CharField(max_length=20, blank=True)
    pages = models.CharField(max_length=50, blank=True)

    @property
    def bibtex_type(self):
        return "article"


class Audio(Reference):
    artist = models.CharField(max_length=255, blank=True)
    album = models.CharField(max_length=255, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    file = models.FileField(upload_to="library_audio/", blank=True, null=True)

    @property
    def bibtex_type(self):
        return "audio"


class Video(Reference):
    director = models.CharField(max_length=255, blank=True)
    producer = models.CharField(max_length=255, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    file = models.FileField(upload_to="library_video/", blank=True, null=True)

    @property
    def bibtex_type(self):
        return "video"