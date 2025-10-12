from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils.text import slugify
import jsonschema
import uuid
from django.template import Template, Context


class Language(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name


class BasePage(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    language = models.ForeignKey(Language, on_delete=models.CASCADE)

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.name} ({self.language.slug})"


class StaticPage(BasePage):
    html_file = models.FileField(upload_to='uploads/html/static/')

    def get_html_content(self):
        if self.html_file:
            return self.html_file.read().decode('utf-8')
        return ""


class PageGenerator(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True, blank=True)
    html_template_file = models.FileField(upload_to='uploads/html/templates/')
    json_schema = models.JSONField(blank=True, null=True)
    check_schema = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def get_template_content(self):
        if self.html_template_file:
            return self.html_template_file.read().decode('utf-8')
        return ""

    def __str__(self):
        return self.name



class DynamicPage(BasePage):
    generator = models.ForeignKey(PageGenerator, on_delete=models.PROTECT)
    config_json = models.JSONField()

    def clean(self):
        if self.generator.check_schema and self.generator.json_schema:
            try:
                jsonschema.validate(instance=self.config_json, schema=self.generator.json_schema)
            except jsonschema.ValidationError as e:
                raise ValidationError({'config_json': _(str(e))})

    @staticmethod
    def _render_template_from_string(template_text, context=None):
        context = context or {}
        template = Template(template_text)
        return template.render(Context(context))

    def render_to_string(self):
        template_text = self.generator.get_template_content()
        return self._render_template_from_string(template_text, self.config_json)


class Image(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    image = models.ImageField(upload_to='uploads/images/')
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            self.slug = f"{base}-{uuid.uuid4().hex[:6]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
