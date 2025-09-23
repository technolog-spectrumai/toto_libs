from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
from django_jsonform.models.fields import JSONField
import reversion
from polymorphic.models import PolymorphicModel


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



class BasePreset(PolymorphicModel):
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name = "Preset"
        verbose_name_plural = "Presets"

    def __str__(self):
        return f"{self.name} [{self.__class__.__name__}]"



# ────────────────────────────────────────────────
# 🧪 LaTeX Preset
# ────────────────────────────────────────────────

class LatexPreset(BasePreset):
    document_class = models.CharField(max_length=100, default='article')
    preamble = models.TextField(blank=True)
    packages = JSONField(schema={"type": "array", "items": {"type": "string"}}, default=list)
    footer_note = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} [LaTeX]"

# ────────────────────────────────────────────────
# 🌐 HTML Preset
# ────────────────────────────────────────────────

class HTMLPreset(BasePreset):
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

    def __str__(self):
        return f"{self.name} [HTML]"

# ────────────────────────────────────────────────
# 📄 Document Model
# ────────────────────────────────────────────────

@reversion.register()
class Document(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    version = models.CharField(max_length=10, default="1.0")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    tags = models.ManyToManyField(Tag, blank=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    content = models.TextField(blank=True)
    preset = models.ForeignKey(BasePreset, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.title} (v{self.version})"

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
                self.version = "1.0"

        super().save(*args, **kwargs)

# ────────────────────────────────────────────────
# 📑 Document Sections & Subsections
# ────────────────────────────────────────────────

class DocumentItem(models.Model):
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255)

    class Meta:
        abstract = True
        ordering = ['order']

    def __str__(self):
        return f"{self.__class__.__name__} {self.order}: {self.title}"

class DocumentSection(DocumentItem):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='sections')
    content = models.TextField(blank=True)

    @property
    def has_subsections(self):
        return self.subsections.exists()

class DocumentSubSection(DocumentItem):
    section = models.ForeignKey(DocumentSection, on_delete=models.CASCADE, related_name='subsections')
    content = models.TextField(blank=True)

# ────────────────────────────────────────────────
# 📎 Compiled Outputs
# ────────────────────────────────────────────────

class PDFFile(models.Model):
    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name='pdf_file')
    file = models.FileField(upload_to='compiled_pdfs/')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"PDF for {self.document.title} ({self.created_at.date()})"

class HTMLFile(models.Model):
    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name='html_file')
    preset = models.ForeignKey(HTMLPreset, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    content = models.TextField(blank=True)
    summary = models.CharField(max_length=1024, blank=True)

    def __str__(self):
        return f"HTML for {self.document.title} ({self.created_at.date()})"
