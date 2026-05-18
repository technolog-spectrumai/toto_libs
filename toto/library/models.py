from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from toto.core.domain import DomainEntity
from toto.vault.models import VaultFile
from toto.palimpsest.models import Tag


# ────────────────────────────────────────────────
# SHARED BASE
# ────────────────────────────────────────────────

class LibraryItem(DomainEntity):
    title = models.CharField(max_length=500)
    authors = models.CharField(max_length=500, blank=True, help_text="Free-text author list, e.g. 'Knuth, D. E.' or 'Bach, J. S. and Handel, G. F.'")
    year = models.PositiveIntegerField(null=True, blank=True)
    doi = models.CharField(max_length=255, blank=True)
    url = models.URLField(blank=True)
    abstract = models.TextField(blank=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="%(class)s_items")
    slug = models.SlugField(unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    vault_file = models.ForeignKey(
        VaultFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(class)s_items",
    )

    class Meta:
        abstract = True
        ordering = ["-year", "title"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    def author_display(self):
        return self.authors or "Unknown"


# ────────────────────────────────────────────────
# BOOK
# ────────────────────────────────────────────────

class Book(LibraryItem):
    publisher = models.CharField(max_length=255, blank=True)
    edition = models.CharField(max_length=50, blank=True)
    isbn = models.CharField(max_length=20, blank=True)

    class Meta(LibraryItem.Meta):
        verbose_name = "Book"
        verbose_name_plural = "Books"

    def get_absolute_url(self):
        return reverse("library:book_detail", args=[self.slug])


# ────────────────────────────────────────────────
# ARTICLE
# ────────────────────────────────────────────────

class Article(LibraryItem):
    journal = models.CharField(max_length=255, blank=True)
    volume = models.CharField(max_length=20, blank=True)
    issue = models.CharField(max_length=20, blank=True)
    pages = models.CharField(max_length=50, blank=True)

    class Meta(LibraryItem.Meta):
        verbose_name = "Article"
        verbose_name_plural = "Articles"

    def get_absolute_url(self):
        return reverse("library:article_detail", args=[self.slug])


# ────────────────────────────────────────────────
# AUDIO REFERENCE
# ────────────────────────────────────────────────

class AudioReference(LibraryItem):
    platform = models.CharField(max_length=255, blank=True, help_text="e.g. Spotify, Apple Podcasts, Audible")
    duration = models.CharField(max_length=50, blank=True, help_text="e.g. 1h 23m")

    class Meta(LibraryItem.Meta):
        verbose_name = "Audio Reference"
        verbose_name_plural = "Audio References"


# ────────────────────────────────────────────────
# VIDEO REFERENCE
# ────────────────────────────────────────────────

class VideoReference(LibraryItem):
    platform = models.CharField(max_length=255, blank=True, help_text="e.g. YouTube, Vimeo, Coursera")
    director = models.CharField(max_length=500, blank=True)
    duration = models.CharField(max_length=50, blank=True, help_text="e.g. 45m")

    class Meta(LibraryItem.Meta):
        verbose_name = "Video Reference"
        verbose_name_plural = "Video References"


# ────────────────────────────────────────────────
# LIBRARY COLLECTION
# ────────────────────────────────────────────────

class LibraryCollection(models.Model):
    title = models.CharField(max_length=500)
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    books = models.ManyToManyField(Book, blank=True, related_name="collections")
    articles = models.ManyToManyField(Article, blank=True, related_name="collections")
    audio = models.ManyToManyField(AudioReference, blank=True, related_name="collections")
    videos = models.ManyToManyField(VideoReference, blank=True, related_name="collections")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Library Collection"
        verbose_name_plural = "Library Collections"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    def all_items(self):
        return [
            *self.books.all(),
            *self.articles.all(),
            *self.audio.all(),
            *self.videos.all(),
        ]
