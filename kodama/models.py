from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


class Site(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    domain = models.URLField(unique=True)
    description = models.TextField(blank=True)
    creation_year = models.PositiveIntegerField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='owned_sites'
    )

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            counter = 1
            while Site.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='tag_set')

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='category_set')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            counter = 1
            while Category.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class Menu(models.Model):
    site = models.ForeignKey('Site', on_delete=models.CASCADE, related_name='menus')
    title = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    categories = models.ManyToManyField(Category, through='CategoryLink', related_name='menus')
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.title} ({self.site.name})"

class CategoryLink(models.Model):
    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name='category_links')
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('menu', 'category')
        ordering = ['order']

    def __str__(self):
        return f"{self.category.name} in {self.menu.title}"


class Article(models.Model):
    slug = models.SlugField(max_length=255, primary_key=True, unique=True)
    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name='articles'
    )
    title = models.CharField(max_length=255)
    summary = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    author = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='authored_articles'
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name='articles')
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='articles'
    )

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title)
            slug = base_slug
            counter = 1
            while Article.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class Section(models.Model):
    article = models.ForeignKey(
        Article,
        on_delete=models.CASCADE,
        related_name='sections'
    )
    order = models.PositiveIntegerField()
    heading = models.CharField(max_length=255)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.article.title} – Section {self.order}: {self.heading}"


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
        Image,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subsections'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        section_order = self.section.order if self.section else '?'
        return f"{self.section.heading} – SubSection {self.order}.{section_order}: {self.title}"
