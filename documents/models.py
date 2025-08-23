from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class Office(models.Model):
    name = models.CharField(max_length=255)
    seal = models.ImageField(upload_to='office_seals/', blank=True, null=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_offices')

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
    document_type = models.CharField(max_length=50, choices=DOCUMENT_TYPES)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Draft')
    created_at = models.DateTimeField(auto_now_add=True)
    office = models.ForeignKey('Office', on_delete=models.CASCADE, related_name='documents')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='authored_documents')
    tags = models.ManyToManyField('Tag', blank=True, related_name='documents')

    def __str__(self):
        return f"{self.title} ({self.document_type})"

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
