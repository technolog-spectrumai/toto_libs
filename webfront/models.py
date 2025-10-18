from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils.text import slugify
import jsonschema
from django.template import Template, Context
from vault.models import VaultFile
from django.core.files.base import ContentFile


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


class WebPage(BasePage):
    html_file = models.ForeignKey(VaultFile, on_delete=models.SET_NULL, null=True, blank=True)

    def get_html_content(self):
        if self.html_file and self.html_file.file:
            return self.html_file.file.read().decode('utf-8')
        return ""


class PageTemplate(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True, blank=True)
    html_template_file = models.ForeignKey(VaultFile, on_delete=models.SET_NULL, null=True, blank=True)
    json_schema = models.JSONField(blank=True, null=True)
    check_schema = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def get_template_content(self):
        if self.html_template_file and self.html_template_file.file:
            return self.html_template_file.file.read().decode('utf-8')
        return ""

    def __str__(self):
        return self.name


class PageGenerator(BasePage):
    template = models.ForeignKey(PageTemplate, on_delete=models.PROTECT)
    config_json = models.JSONField()

    def clean(self):
        if self.template.check_schema and self.template.json_schema:
            try:
                jsonschema.validate(instance=self.config_json, schema=self.template.json_schema)
            except jsonschema.ValidationError as e:
                raise ValidationError({'config_json': _(str(e))})

    @staticmethod
    def _render_template_from_string(template_text, context=None):
        context = context or {}
        template = Template(template_text)
        return template.render(Context(context))

    def render_to_string(self):
        template_text = self.template.get_template_content()
        return self._render_template_from_string(template_text, self.config_json)

    def bake_to_static(self):
        html_content = self.render_to_string()
        vault_file = VaultFile.objects.create(
            owner=self.language,
            title=f"{self.name}.html",
            file_type='text',
            notes=f"Baked from PageGenerator {self.slug}"
        )
        vault_file.file.save(f"{self.slug}.html", ContentFile(html_content))
        vault_file.save()

        web_page = WebPage.objects.create(
            name=self.name,
            slug=self.slug,
            language=self.language,
            html_file=vault_file
        )
        return web_page
