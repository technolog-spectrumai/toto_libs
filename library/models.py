# models.py
from django.db import models
from polymorphic.models import PolymorphicModel
from vault.models import VaultFile


# ────────────────────────────────────────────────
# 🔖 Base Reference Item (Polymorphic)
# ────────────────────────────────────────────────

class ReferenceItem(PolymorphicModel):
    """
    Base class for all reference items (book, journal, video, audio, website, generic, etc.)
    """
    title = models.CharField(max_length=500)
    order = models.PositiveIntegerField(default=0)

    # Tags instead of Bibliography
    tags = models.ManyToManyField(
        'ReferenceTag',
        blank=True,
        related_name='references'
    )
    vault_file = models.ForeignKey(
        VaultFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reference_items",
        help_text="Optional link to a file stored in the vault"
    )

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.__class__.__name__}: {self.title}"


class ReferenceTag(models.Model):
    """
    Simple tag model to categorize references.
    """
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


# ────────────────────────────────────────────────
# 📖 Book Reference
# ────────────────────────────────────────────────

class BookReference(ReferenceItem):
    author = models.CharField(max_length=255)
    publisher = models.CharField(max_length=255, blank=True)
    year = models.CharField(max_length=10, blank=True)
    isbn = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f"Book: {self.author}, {self.title} ({self.year})"

    def to_latex(self):
        return f"""@book{{book{self.pk},
          author = {{{self.author}}},
          title = {{{self.title}}},
          publisher = {{{self.publisher}}},
          year = {{{self.year}}},
          isbn = {{{self.isbn}}}
        }}"""


# 📰 Journal Reference
class JournalReference(ReferenceItem):
    author = models.CharField(max_length=255)
    journal = models.CharField(max_length=255)
    volume = models.CharField(max_length=50, blank=True)
    issue = models.CharField(max_length=50, blank=True)
    pages = models.CharField(max_length=50, blank=True)
    year = models.CharField(max_length=10, blank=True)
    doi = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"Journal: {self.author}, {self.title}, {self.journal} ({self.year})"

    def to_latex(self):
        return f"""@article{{journal{self.pk},
          author = {{{self.author}}},
          title = {{{self.title}}},
          journal = {{{self.journal}}},
          volume = {{{self.volume}}},
          number = {{{self.issue}}},
          pages = {{{self.pages}}},
          year = {{{self.year}}},
          doi = {{{self.doi}}}
        }}"""


# 🎥 Video Reference
class VideoReference(ReferenceItem):
    creator = models.CharField(max_length=255, blank=True)
    platform = models.CharField(max_length=100, blank=True)  # e.g. YouTube, Vimeo
    url = models.URLField()
    year = models.CharField(max_length=10, blank=True)

    def __str__(self):
        return f"Video: {self.title} [{self.platform}]"

    def to_latex(self):
        return f"""@misc{{video{self.pk},
          title = {{{self.title}}},
          author = {{{self.creator}}},
          howpublished = {{\\url{{{self.url}}}}},
          note = {{{self.platform}}},
          year = {{{self.year}}}
        }}"""


# 🎵 Audio Reference
class AudioReference(ReferenceItem):
    artist = models.CharField(max_length=255, blank=True)
    album = models.CharField(max_length=255, blank=True)
    url = models.URLField()
    year = models.CharField(max_length=10, blank=True)

    def __str__(self):
        return f"Audio: {self.artist} - {self.title} ({self.year})"

    def to_latex(self):
        return f"""@misc{{audio{self.pk},
          title = {{{self.title}}},
          author = {{{self.artist}}},
          howpublished = {{\\url{{{self.url}}}}},
          note = {{{self.album}}},
          year = {{{self.year}}}
        }}"""


# 🌐 Website Reference
class WebsiteReference(ReferenceItem):
    author = models.CharField(max_length=255, blank=True)
    sitename = models.CharField(max_length=255, blank=True)   # e.g. "BBC News"
    url = models.URLField()
    accessed_date = models.DateField(blank=True, null=True)
    year = models.CharField(max_length=10, blank=True)

    def __str__(self):
        return f"Website: {self.sitename or self.url} ({self.year})"

    def to_latex(self):
        return f"""@misc{{website{self.pk},
          author = {{{self.author}}},
          title = {{{self.title}}},
          howpublished = {{\\url{{{self.url}}}}},
          note = {{{self.sitename}}},
          year = {{{self.year}}},
          accessed = {{{self.accessed_date}}}
        }}"""


# 🗂 Generic Reference
class GenericReference(ReferenceItem):
    description = models.TextField(blank=True)
    url = models.URLField(blank=True)
    year = models.CharField(max_length=10, blank=True)
    author = models.CharField(max_length=255, blank=True)
    sourcetype = models.CharField(max_length=100, blank=True)  # e.g. Dataset, Report

    def __str__(self):
        return f"Generic: {self.title} ({self.sourcetype})"

    def to_latex(self):
        return f"""@misc{{generic{self.pk},
          author = {{{self.author}}},
          title = {{{self.title}}},
          howpublished = {{\\url{{{self.url}}}}},
          note = {{{self.sourcetype}}},
          year = {{{self.year}}},
          description = {{{self.description}}}
        }}"""