from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
import os
from django.conf import settings
from mermaid_cli import render_mermaid_file_sync
import matplotlib.pyplot as plt
from polymorphic.models import PolymorphicModel


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


class Document(PolymorphicModel):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    department = models.ForeignKey('Department', on_delete=models.SET_NULL, null=True, blank=True)
    tags = models.ManyToManyField('Tag', blank=True)
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=50, choices=[
        ('Draft', 'Draft'),
        ('Active', 'Active'),
        ('Deprecated', 'Deprecated'),
        ('Archived', 'Archived'),
    ], default='Draft')
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def document_type(self):
        return self.get_real_instance_class().get_type()


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

    def __str__(self):
        return f"{self.title} ({self.get_real_instance_class().__name__})"

    @staticmethod
    def get_type():
        return "Generic"


class HtmlDocument(Document):
    summary = models.TextField(blank=True)

    class Meta:
        verbose_name = "HTML Document"
        verbose_name_plural = "HTML Documents"

    @staticmethod
    def get_type():
        return "HTML"


class LatexDocument(Document):
    compile_flags = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "LaTeX Document"
        verbose_name_plural = "LaTeX Documents"

    @staticmethod
    def get_type():
        return "Latex"


class Section(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='sections')
    order = models.PositiveIntegerField()
    heading = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.document.title} – Section {self.order}: {self.heading}"

    @property
    def all_subsections(self):
        return sorted(
            list(self.html_subsections.all()) + list(self.latex_subsections.all()),
            key=lambda s: s.order
        )


class HTMLSubSection(models.Model):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='html_subsections')
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    content = models.TextField()
    image = models.ForeignKey('Image', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.section.heading} – HTML SubSection {self.order}: {self.title}"

class LaTeXSubSection(models.Model):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='latex_subsections')
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    use_light_mode = models.BooleanField(default=False)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.section.heading} – LaTeX SubSection {self.order}: {self.title}"


class Image(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.ImageField(upload_to='subsection_images/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


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


class Formula(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    latex_code = models.TextField(help_text="Enter valid LaTeX code here.")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    def render_image(self):
        output_dir = os.path.join(settings.MEDIA_ROOT, 'formulas')
        os.makedirs(output_dir, exist_ok=True)

        output_path = os.path.join(output_dir, f"{self.id}.png")

        plt.rc('text', usetex=True)
        plt.figure(figsize=(4, 1))
        plt.text(0.5, 0.5, f"${self.latex_code}$", fontsize=20, ha='center', va='center')
        plt.axis('off')
        plt.savefig(output_path, bbox_inches='tight', pad_inches=0.1)
        plt.close()

        return output_path


class Scratchpad(models.Model):
    CONTENT_TYPE_CHOICES = [
        ('plain', 'Plain Text'),
        ('html', 'HTML'),
        ('latex', 'LaTeX'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='scratchpads')
    title = models.CharField(max_length=255, blank=True)
    content = models.TextField(blank=True)
    content_type = models.CharField(max_length=10, choices=CONTENT_TYPE_CHOICES, default='plain')
    created_at = models.DateTimeField(auto_now_add=True)
    use_light_mode = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title or f"Scratchpad #{self.pk}"




