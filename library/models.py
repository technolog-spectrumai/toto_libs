# models.py
from django.db import models
from polymorphic.models import PolymorphicModel
from vault.models import VaultFile
from django.contrib.auth import get_user_model
from django.forms.models import model_to_dict
from datetime import date, datetime
from community.models import CommunityMember

User = get_user_model()


class Library(models.Model):
    """
    A collection of reference items owned by a user.
    """
    owner = models.ForeignKey(
        CommunityMember,
        on_delete=models.CASCADE,
        related_name="libraries",
        db_comment="owned_by"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # A library can contain many reference items
    references = models.ManyToManyField(
        'ReferenceItem',
        related_name='libraries',
        blank=True,
        db_comment="contains_references"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (Owner: {self.owner})"

        # ────────────────────────────────────────────────
        # 📚 Export Library to BibTeX
        # ────────────────────────────────────────────────

    def to_json(self):
        items = self.references.all().order_by("order", "title")
        return {
            "library": self.name,
            "description": self.description,
            "owner": self.owner.username,
            "references": [item.to_json() for item in items]
        }


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
        related_name='references',
        db_comment="tagged_by"
    )
    vault_file = models.ForeignKey(
        VaultFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reference_items",
        help_text="Optional link to a file stored in the vault",
        db_comment="linked_vault_file"
    )

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.__class__.__name__}: {self.title}"

    def to_json(self):
        data = model_to_dict(self)
        for key, value in data.items():
            if isinstance(value, (date, datetime)):
                data[key] = value.isoformat()
        # Add polymorphic type
        data["type"] = self.__class__.__name__

        # Convert tags to names
        data["tags"] = [tag.name for tag in self.tags.all()]

        # Convert vault file to ID
        data["vault_file"] = self.vault_file_id

        return data


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
    sitename = models.CharField(max_length=255, blank=True)   # e.g. "BBC News"
    url = models.URLField()
    accessed_date = models.DateField(blank=True, null=True)
    year = models.CharField(max_length=10, blank=True)

    def __str__(self):
        return f"Website: {self.sitename or self.url} ({self.year})"


# 🗂 Generic Reference
class GenericReference(ReferenceItem):
    description = models.TextField(blank=True)
    url = models.URLField(blank=True)
    year = models.CharField(max_length=10, blank=True)
    author = models.CharField(max_length=255, blank=True)
    sourcetype = models.CharField(max_length=100, blank=True)  # e.g. Dataset, Report

    def __str__(self):
        return f"Generic: {self.title} ({self.sourcetype})"