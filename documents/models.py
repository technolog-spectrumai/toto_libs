from django.db import models
from django.contrib.auth.models import User
from polymorphic.models import PolymorphicModel
from django_jsonform.models.fields import JSONField
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils.text import slugify

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

    def __str__(self):
        return self.name

# ────────────────────────────────────────────────
# ⚙️ Preset Base Model (Polymorphic)
# ────────────────────────────────────────────────

class Preset(PolymorphicModel):
    ENGINE_CHOICES = [
        ('latex', 'LaTeX'),
        ('html', 'HTML'),
    ]

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    engine = models.CharField(max_length=10, choices=ENGINE_CHOICES)
    depth = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(3)],
        help_text="Depth level from 1 (shallow) to 3 (deep)"
    )

    def __str__(self):
        return f"{self.name} [{self.engine}]"

# ────────────────────────────────────────────────
# 🧪 LaTeX Preset
# ────────────────────────────────────────────────

class LatexPreset(Preset):
    document_class = models.CharField(max_length=100, default='article')
    preamble = models.TextField(blank=True)
    packages = JSONField(
        schema={
            "type": "array",
            "items": {"type": "string"},
            "title": "LaTeX Packages"
        },
        default=list,
        help_text="List of LaTeX packages to include"
    )
    footer_note = models.TextField(blank=True)

# ────────────────────────────────────────────────
# 🌐 HTML Preset
# ────────────────────────────────────────────────

class HTMLPreset(Preset):
    template_name = models.CharField(max_length=255)
    css_classes = JSONField(
        schema={
            'type': 'dict',
            'keys': {'name': {'type': 'string'}},
            "title": "CSS Classes by Element",
            "additionalProperties": {"type": "string"}
        },
        default=dict,
        help_text="CSS classes for HTML elements"
    )
    script_includes = JSONField(
        schema={
            "type": "array",
            "items": {"type": "string", "format": "uri"},
            "title": "Script URLs"
        },
        default=list,
        help_text="List of JS script URLs to include"
    )
    footer_html = models.TextField(blank=True)

# ────────────────────────────────────────────────
# 📄 Document Model
# ────────────────────────────────────────────────

class Document(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    preset = models.ForeignKey(Preset, on_delete=models.SET_NULL, null=True)
    summary = models.TextField(blank=True, help_text="Brief summary of the document")
    version = models.CharField(max_length=10, default="1.0", help_text="Version number in major.minor format")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    tags = models.ManyToManyField(Tag, blank=True, related_name='documents')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='documents')
    content = models.TextField(blank=True, null=True, help_text="Plain text content of the document")

    def __str__(self):
        return f"{self.title} (v{self.version})"

    @property
    def depth(self):
        return self.preset.depth if self.preset else None

    @property
    def engine(self):
        return self.preset.engine if self.preset else None

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title)
            slug = base_slug
            counter = 1
            while Document.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug

        if not self.pk:
            latest = Document.objects.filter(created_by=self.created_by).order_by('-created_at').first()
            if latest:
                major, minor = map(int, latest.version.split('.'))
                self.version = f"{major}.{minor + 1}"
            else:
                self.version = "1.1"

        super().save(*args, **kwargs)

# ────────────────────────────────────────────────
# 📚 Section Model
# ────────────────────────────────────────────────

class Section(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='sections')
    title = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)
    content = models.TextField(blank=True, null=True, help_text="Plain text content of the section")

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"Section {self.order}: {self.title}"

# ────────────────────────────────────────────────
# 📘 SubSection Model
# ────────────────────────────────────────────────

class SubSection(models.Model):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='subsections')
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True, null=True, help_text="Plain text content of the subsection")

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"SubSection {self.order}: {self.title}"
