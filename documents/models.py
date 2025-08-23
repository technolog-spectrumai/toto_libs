from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
from weasyprint import HTML
from django.template.loader import render_to_string
import tempfile
from django.utils.timezone import now
from django.conf import settings
import os


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class Department(models.Model):
    name = models.CharField(max_length=255)
    seal = models.ImageField(upload_to='department_seals/', blank=True, null=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_departments')

    def __str__(self):
        return self.name


class Document(models.Model):
    DOCUMENT_TYPES = [
        ('Analysis', 'Analysis'),
        ('Report', 'Report'),
        ('Design', 'Design'),
        ('Plan', 'Plan'),
    ]

    STATUS_CHOICES = [
        ('Draft', 'Draft'),
        ('Active', 'Active'),
        ('Deprecated', 'Deprecated'),
        ('Archived', 'Archived'),
    ]

    slug = models.SlugField(max_length=255, primary_key=True, unique=True)
    title = models.CharField(max_length=255)
    summary = models.TextField()
    type = models.CharField(max_length=50, choices=DOCUMENT_TYPES)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Draft')
    created_at = models.DateTimeField(auto_now_add=True)
    department = models.ForeignKey('Department', on_delete=models.CASCADE, related_name='documents')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='authored_documents')
    tags = models.ManyToManyField('Tag', blank=True, related_name='documents')

    def __str__(self):
        return f"{self.title} ({self.type})"

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title)
            slug = base_slug
            counter = 1
            while Document.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def generate_pdf(self):
        seal_path = None
        if self.department.seal:
            seal_path = os.path.join(settings.MEDIA_ROOT, self.department.seal.name)
        html_string = render_to_string("documents/pdf.html", {
            "document": self,
            "sections": self.sections.all(),
            "copyright_holder": "SpectrumAi.pl",
            "now": now(),
            "seal_path": seal_path
        })

        # Use MEDIA_ROOT as base_url so WeasyPrint can resolve image paths
        media_root = settings.MEDIA_ROOT
        base_url = os.path.join(media_root)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as output:
            HTML(string=html_string, base_url=".").write_pdf(output.name)
            return output.name


class Section(models.Model):
    report = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='sections',
        limit_choices_to={'type': 'Report'}
    )
    order = models.PositiveIntegerField()
    heading = models.CharField(max_length=255)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.report.title} – Section {self.order}: {self.heading}"


class SubSection(models.Model):
    section = models.ForeignKey(
        Section,
        on_delete=models.CASCADE,
        related_name='subsections'
    )
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.section.heading} – SubSection {self.order}: {self.title}"

