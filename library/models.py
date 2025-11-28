from django.db import models
from polymorphic.models import PolymorphicModel

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


# 🎥 Video Reference
class VideoReference(ReferenceItem):
    creator = models.CharField(max_length=255, blank=True)
    platform = models.CharField(max_length=100, blank=True)  # e.g. YouTube, Vimeo
    url = models.URLField()
    year = models.CharField(max_length=10, blank=True)

    def __str__(self):
        return f"Video: {self.title} [{self.platform}]"


# 🎵 Audio Reference
class AudioReference(ReferenceItem):
    artist = models.CharField(max_length=255, blank=True)
    album = models.CharField(max_length=255, blank=True)
    url = models.URLField()
    year = models.CharField(max_length=10, blank=True)

    def __str__(self):
        return f"Audio: {self.artist} - {self.title} ({self.year})"


# 🌐 Website Reference
class WebsiteReference(ReferenceItem):
    author = models.CharField(max_length=255, blank=True)
    site_name = models.CharField(max_length=255, blank=True)   # e.g. "BBC News"
    url = models.URLField()
    accessed_date = models.DateField(blank=True, null=True)
    year = models.CharField(max_length=10, blank=True)

    def __str__(self):
        return f"Website: {self.site_name or self.url} ({self.year})"


# 🗂 Generic Reference
class GenericReference(ReferenceItem):
    description = models.TextField(blank=True)
    url = models.URLField(blank=True)
    year = models.CharField(max_length=10, blank=True)
    author = models.CharField(max_length=255, blank=True)
    source_type = models.CharField(max_length=100, blank=True)  # e.g. Dataset, Report

    def __str__(self):
        return f"Generic: {self.title} ({self.source_type})"
