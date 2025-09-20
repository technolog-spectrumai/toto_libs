from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
from django_jsonform.models.fields import JSONField
from polymorphic.models import PolymorphicModel
import reversion

# ────────────────────────────────────────────────
# 🔖 Tag Model
# ────────────────────────────────────────────────

class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name

# ────────────────────────────────────────────────
# 🏢 Department Model
# ────────────────────────────────────────────────

class Department(models.Model):
    name = models.CharField(max_length=255)
    seal = models.ImageField(upload_to='department_seals/', blank=True, null=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_departments')

    copyright_holder = models.CharField(
        max_length=255,
        default="SpectrumAi.pl"
    )
    copyright_notice = models.TextField(
        default=(
            "This document is confidential and intended solely for the use of the individual or entity to whom it is addressed. "
            "Unauthorized distribution, reproduction, or disclosure is strictly prohibited."
        )
    )

    def __str__(self):
        return self.name

# ────────────────────────────────────────────────
# 🧩 Abstract Base Document
# ────────────────────────────────────────────────

@reversion.register()
class BaseDocument(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    version = models.CharField(max_length=10, default="1.0")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    tags = models.ManyToManyField(Tag, blank=True, related_name='%(class)s_tags')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='%(class)s_documents')

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.title} (v{self.version})"

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title)
            slug = base_slug
            counter = 1
            while type(self).objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug

        if not self.pk:
            latest = type(self).objects.filter(created_by=self.created_by).order_by('-created_at').first()
            if latest:
                major, minor = map(int, latest.version.split('.'))
                self.version = f"{major}.{minor + 1}"
            else:
                self.version = "1.0"

        super().save(*args, **kwargs)

# ────────────────────────────────────────────────
# 🧪 LaTeX Preset
# ────────────────────────────────────────────────

class LatexPreset(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    document_class = models.CharField(max_length=100, default='article')
    preamble = models.TextField(blank=True)
    packages = JSONField(
        schema={"type": "array", "items": {"type": "string"}},
        default=list
    )
    footer_note = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} [LaTeX]"

# ────────────────────────────────────────────────
# 🌐 HTML Preset
# ────────────────────────────────────────────────

class HTMLPreset(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    css_classes = JSONField(
        schema={
            'type': 'dict',
            'keys': {'name': {'type': 'string'}},
            "additionalProperties": {"type": "string"}
        },
        default=dict
    )
    style_mapping = JSONField(
        schema={
            'type': 'object',
            'properties': {
                'h1': {'type': 'string'},
                'h2': {'type': 'string'},
                'h3': {'type': 'string'},
                'p': {'type': 'string'},
                'div.summary': {'type': 'string'}
            },
            'additionalProperties': {'type': 'string'}
        },
        default={
            "h1": "text-4xl font-bold mt-8 mb-4",
            "h2": "text-3xl font-semibold mt-6 mb-3",
            "h3": "text-2xl font-medium mt-4 mb-2",
            "p": "mb-4 leading-relaxed",
            "div.summary": "text-sm italic text-gray-600 dark:text-gray-400 mb-6"
        }
    )
    footer_html = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} [HTML Preset]"

# ────────────────────────────────────────────────
# 📄 Polymorphic Document Base
# ────────────────────────────────────────────────

class Document(PolymorphicModel, BaseDocument):
    summary = models.TextField(blank=True)
    content = models.TextField(blank=True)

    class Meta:
        verbose_name = "Document"
        verbose_name_plural = "Documents"

    def __str__(self):
        return f"{self.title} [{self.get_real_instance_class().__name__}] (v{self.version})"

    @property
    def document_type(self):
        return self.get_real_instance_class().__name__.replace("Document", "")

# ────────────────────────────────────────────────
# 📄 LaTeX Document
# ────────────────────────────────────────────────

class LatexDocument(Document):
    preset = models.ForeignKey(LatexPreset, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        verbose_name = "LaTeX Document"
        verbose_name_plural = "LaTeX Documents"

# ────────────────────────────────────────────────
# 🌐 HTML Document
# ────────────────────────────────────────────────

class HTMLDocument(Document):
    preset = models.ForeignKey(HTMLPreset, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        verbose_name = "HTML Document"
        verbose_name_plural = "HTML Documents"

    @property
    def html(self):
        styled = self.content or ""
        if self.preset and self.preset.style_mapping:
            for tag, classes in self.preset.style_mapping.items():
                if '.' in tag:
                    tag1, class1 = tag.split('.')
                    styled = styled.replace(
                        f"<{tag1} class='{class1}'>",
                        f"<{tag} class='{classes}'>"
                    )
                else:
                    styled = styled.replace(
                        f"<{tag}>",
                        f"<{tag} class='{classes}'>"
                    )
        return styled

# ────────────────────────────────────────────────
# 📎 PDF File for LaTeX Documents
# ────────────────────────────────────────────────

class PDFFile(models.Model):
    document = models.OneToOneField(
        LatexDocument,
        on_delete=models.CASCADE,
        related_name='pdf_file'
    )
    file = models.FileField(upload_to='compiled_pdfs/')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"PDF for {self.document.title} (created {self.created_at.strftime('%Y-%m-%d')})"


class DocumentEditor(BaseDocument):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='editors')

    def __str__(self):
        return f"Editor for {self.document.title} (v{self.version})"


class EditorItem(models.Model):
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255)

    class Meta:
        abstract = True
        ordering = ['order']

    def __str__(self):
        return f"{self.__class__.__name__} {self.order}: {self.title}"



class EditorSection(EditorItem):
    document = models.ForeignKey(DocumentEditor, on_delete=models.CASCADE, related_name='sections')
    content = models.TextField(blank=True)

    @property
    def deep(self):
        return self.subsections.exists()


class EditorSubSection(EditorItem):
    section = models.ForeignKey(EditorSection, on_delete=models.CASCADE, related_name='subsections')
    content = models.TextField(blank=True)

