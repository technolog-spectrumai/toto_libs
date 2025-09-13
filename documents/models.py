from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
from django_jsonform.models.fields import JSONField

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
    template_name = models.CharField(max_length=255)
    css_classes = JSONField(
        schema={
            'type': 'dict',
            'keys': {'name': {'type': 'string'}},
            "additionalProperties": {"type": "string"}
        },
        default=dict
    )
    footer_html = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} [HTML]"


# ────────────────────────────────────────────────
# 📄 Abstract BaseDocument
# ────────────────────────────────────────────────

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
# 📚 Abstract DocumentItem
# ────────────────────────────────────────────────

class DocumentItem(models.Model):
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255)
    latex_content = models.TextField(blank=True, null=True)
    html_content = models.TextField(blank=True, null=True)

    class Meta:
        abstract = True
        ordering = ['order']

    @property
    def content(self):
        return self.html_content or self.latex_content

    @property
    def latex(self):
        return self.latex_content or ""

    def __str__(self):
        return f"{self.__class__.__name__} {self.order}: {self.title}"


# 📄 Document Model
class Document(BaseDocument):
    latex_preset = models.ForeignKey(LatexPreset, on_delete=models.SET_NULL, null=True, blank=True)
    html_preset = models.ForeignKey(HTMLPreset, on_delete=models.SET_NULL, null=True, blank=True)
    latex_summary = models.TextField(blank=True)
    html_summary = models.TextField(blank=True)

    @property
    def summary(self):
        return self.html_summary

    @property
    def deep(self):
        return self.sections.exists()

# 📚 Section Model
class Section(DocumentItem):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='sections')

    @property
    def deep(self):
        return self.subsections.exists()

# 📘 SubSection Model
class SubSection(DocumentItem):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='subsections')
