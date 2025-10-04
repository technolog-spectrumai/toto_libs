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



class LatexPreset(models.Model):
    name = models.CharField(max_length=100)
    document_class = models.CharField(max_length=100, default='article')
    preamble = models.TextField(blank=True)
    packages = JSONField(schema={"type": "array", "items": {"type": "string"}}, default=list)
    footer_note = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} [LaTeX]"


@reversion.register()
class Document(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    version = models.CharField(max_length=10, default="1.0")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    tags = models.ManyToManyField(Tag, blank=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    preset = models.ForeignKey(LatexPreset, on_delete=models.SET_NULL, null=True, blank=True)


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
    is_raw = models.BooleanField(default=False)

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
