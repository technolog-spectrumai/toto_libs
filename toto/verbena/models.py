from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from trix_editor.fields import TrixEditorField
from toto.core.domain import DomainEntity
from toto.socialhub.models import Person
from toto.vault.models import VaultFile

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

    # Bibliography references
    references = models.ManyToManyField("Reference", blank=True, related_name="pages_refs")

    def authors(self):
        return Person.objects.filter(verbena_sections__page=self).distinct()

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("verbena:page_detail", args=[self.slug])


# ────────────────────────────────────────────────
# SECTION
# ────────────────────────────────────────────────

class Section(DomainEntity):
    page = models.ForeignKey(Page, related_name="sections", on_delete=models.CASCADE)
    title = models.CharField(max_length=255, blank=True)
    content = TrixEditorField(blank=True)  # full WYSIWYG, includes headings/images
    author = models.ForeignKey(
        Person, related_name="verbena_sections", on_delete=models.SET_NULL, null=True, blank=True
    )
    order = models.PositiveIntegerField(default=0)
    tags = models.ManyToManyField(Tag, related_name="sections", blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.page.title} – {self.title or 'Section'}"


# ────────────────────────────────────────────────
# REFERENCE (unified for books and articles)
# ────────────────────────────────────────────────

class Reference(DomainEntity):
    title = models.CharField(max_length=500)
    authors = models.ManyToManyField(Person, related_name="references", blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    doi = models.CharField(max_length=255, blank=True)
    url = models.URLField(blank=True)
    abstract = models.TextField(blank=True)
    tags = models.ManyToManyField(Tag, related_name="references", blank=True)
    slug = models.SlugField(unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    vault_file = models.ForeignKey(
        VaultFile, on_delete=models.SET_NULL, null=True, blank=True, related_name="references"
    )

    # Optional fields for books
    publisher = models.CharField(max_length=255, blank=True)
    edition = models.CharField(max_length=50, blank=True)
    isbn = models.CharField(max_length=20, blank=True)

    # Optional fields for articles
    journal = models.CharField(max_length=255, blank=True)
    volume = models.CharField(max_length=20, blank=True)
    issue = models.CharField(max_length=20, blank=True)
    pages = models.CharField(max_length=50, blank=True)

    class Meta:
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
        if self.publisher:
            return "book"
        elif self.journal:
            return "article"
        else:
            return "misc"