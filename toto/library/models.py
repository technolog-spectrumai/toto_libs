from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from toto.core.domain import DomainEntity
from toto.people.models import Person
from toto.vault.models import VaultFile
from toto.verbena.models import Tag


# ────────────────────────────────────────────────
# SHARED BASE
# ────────────────────────────────────────────────

class LibraryItem(DomainEntity):
    title = models.CharField(max_length=500)
    authors = models.ManyToManyField(Person, blank=True, related_name="%(class)s_items")
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
        authors = list(self.authors.all())
        if not authors:
            return "Unknown"
        if len(authors) == 1:
            return authors[0].full_name
        return f"{authors[0].full_name} et al."


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
