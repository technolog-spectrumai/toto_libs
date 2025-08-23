from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
import os
from django.conf import settings
from mermaid_cli import render_mermaid_file_sync


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


class Image(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.ImageField(upload_to='subsection_images/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title



class SubSection(models.Model):
    section = models.ForeignKey(
        Section,
        on_delete=models.CASCADE,
        related_name='subsections'
    )
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    content = models.TextField()
    image = models.ForeignKey(
        'Image',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subsections'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.section.heading} – SubSection {self.order}: {self.title}"


class Diagram(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    code = models.TextField(help_text="Paste valid Mermaid.js syntax here.")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    def render_image(self, output_format='png', theme='default'):
        """
        Renders Mermaid diagram to an image using mermaid-cli (Python).
        Returns the path to the generated image.
        """
        # Define output directory
        output_dir = os.path.join(settings.MEDIA_ROOT, 'diagrams')
        os.makedirs(output_dir, exist_ok=True)

        # Define file paths
        input_path = os.path.join(output_dir, f"diagram{self.pk}.mmd")
        output_path = os.path.join(output_dir, f"diagram{self.pk}.{output_format}")

        # Write Mermaid code to input file
        with open(input_path, 'w') as f:
            f.write(self.code)

        # Render diagram using synchronous wrapper
        render_mermaid_file_sync(
            input_file=input_path,
            output_file=output_path,
            output_format=output_format,
            mermaid_config={"theme": theme}
        )

        return output_path




