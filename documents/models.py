from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
from django_jsonform.models.fields import JSONField
from TexSoup import TexSoup

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
# 🧩 Abstract Base Classes
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


class BaseDocumentItem(models.Model):
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255)

    class Meta:
        abstract = True
        ordering = ['order']

    def __str__(self):
        return f"{self.__class__.__name__} {self.order}: {self.title}"

# ────────────────────────────────────────────────
# 🧪 LaTeX Document Flow
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


class LatexDocument(BaseDocument):
    preset = models.ForeignKey(LatexPreset, on_delete=models.SET_NULL, null=True, blank=True)
    summary = models.TextField(blank=True)

    @property
    def deep(self):
        return self.sections.exists()


class LatexSection(BaseDocumentItem):
    document = models.ForeignKey(LatexDocument, on_delete=models.CASCADE, related_name='sections')
    content = models.TextField(blank=True)

    @property
    def deep(self):
        return self.subsections.exists()


class LatexSubSection(BaseDocumentItem):
    section = models.ForeignKey(LatexSection, on_delete=models.CASCADE, related_name='subsections')
    content = models.TextField(blank=True)

# ────────────────────────────────────────────────
# 🌐 HTML Document Flow
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


class HTMLDocument(BaseDocument):
    preset = models.ForeignKey(HTMLPreset, on_delete=models.SET_NULL, null=True, blank=True)
    summary = models.TextField(blank=True)
    linked_latex = models.ForeignKey(
        'LatexDocument',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='linked_html_versions'
    )

    @property
    def deep(self):
        return self.sections.exists()


class HTMLSection(BaseDocumentItem):
    document = models.ForeignKey(HTMLDocument, on_delete=models.CASCADE, related_name='sections')
    content = models.TextField(blank=True)

    @property
    def deep(self):
        return self.subsections.exists()


class HTMLSubSection(BaseDocumentItem):
    section = models.ForeignKey(HTMLSection, on_delete=models.CASCADE, related_name='subsections')
    content = models.TextField(blank=True)


class LatexDocumentFactory(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='latex_factories')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='latex_factories')
    preset = models.ForeignKey(LatexPreset, on_delete=models.SET_NULL, null=True, blank=True, related_name='latex_factories')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} (Factory)"

    def convert(self, scratchpad):
        document = LatexDocument.objects.create(
            title=scratchpad.name or f"Scratchpad #{scratchpad.pk}",
            slug=slugify(scratchpad.name or f"Scratchpad-{scratchpad.pk}"),
            created_by=scratchpad.author,
            department=self.department,
            preset=self.preset
        )

        self._parse_latex(scratchpad.content, document)
        return document

    def _parse_latex(self, content: str, document: LatexDocument):
        soup = TexSoup(content)
        section_order = 0

        for section_cmd in soup.find_all('section'):
            heading = str(section_cmd.string).strip()
            section = LatexSection.objects.create(
                document=document,
                order=section_order,
                title=heading,
                content=''  # Optional: store raw section content here
            )
            subsection_order = 0
            for sub_cmd in section_cmd.find_all('subsection'):
                title = str(sub_cmd.string).strip()
                body = ''.join(str(x) for x in sub_cmd.contents).strip()
                LatexSubSection.objects.create(
                    section=section,
                    order=subsection_order,
                    title=title,
                    content=body
                )
                subsection_order += 1

            section_order += 1


class LatexScratchpad(models.Model):
    name = models.CharField(max_length=255)
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='latex_scratchpads')
    factory = models.ForeignKey(
        'LatexDocumentFactory',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='scratchpads'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    content = models.TextField()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} by {self.author.username if self.author else 'Unknown'}"

