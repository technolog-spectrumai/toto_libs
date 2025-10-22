# projects/models.py
import subprocess
from django.db import models
from django.contrib.auth.models import User
from vault.models import Bucket, VaultFile
from django.core.files.base import ContentFile
import shutil
from pathlib import Path
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.template import Template, Context
import jsonschema
from django.core.exceptions import ValidationError
from django.core.files import File


class LatexProject(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    bucket = models.ForeignKey(
        Bucket,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='latex_projects'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def compile_all(self):
        compiled = []
        for texfile in self.tex_files.all():
            try:
                result = texfile.compile()
                if result:
                    compiled.append(result)
            except Exception:
                continue
        return compiled

    def get_key(self):
        return self.name.replace(' ', '_')

    def get_dir_path(self):
        key = self.get_key()
        output_dir = Path(settings.MEDIA_ROOT) / f"{key}_files"
        return output_dir

    def clean_directory(self):
        output_dir = self.get_dir_path()
        if output_dir.exists() and output_dir.is_dir():
            shutil.rmtree(output_dir)

    def __str__(self):
        return self.name


class TexFile(models.Model):
    project = models.ForeignKey(
        LatexProject,
        on_delete=models.CASCADE,
        related_name='tex_files'
    )
    filename = models.CharField(max_length=255)
    file = models.FileField(upload_to='tex_sources/', blank=True)  # This is the actual LaTeX source file
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.filename

    @staticmethod
    def run_pdflatex(output_dir, tex_path):
        subprocess.run(
            ['pdflatex', '-interaction=nonstopmode', '-output-directory', output_dir, tex_path],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

    def _compile(self):
        if not self.file:
            raise ValueError("No LaTeX source file to compile.")

        key = self.project.get_key()
        output_dir = self.project.get_dir_path()
        output_dir.mkdir(parents=True, exist_ok=True)
        tex_path = output_dir / f"{key}.tex"
        shutil.copy2(self.file.path, tex_path)

        self.run_pdflatex(output_dir, tex_path)

        aux_file = output_dir / f"{key}.aux"
        if not aux_file.exists():
            self.run_pdflatex(output_dir, tex_path)

        output_pdf = output_dir / self.filename.replace('.tex', '.pdf')
        if output_pdf.exists():
            with open(output_pdf, 'rb') as pdf_file:
                return pdf_file.read()

        return None

    def compile(self):
        try:
            pdf_content = self._compile()
            if not pdf_content:
                raise RuntimeError("PDF file was not generated.")

            vault_file = VaultFile.objects.create(
                owner=self.project.user,
                title=f"{self.filename} (compiled)",
                file_type='pdf',
                bucket=self.project.bucket,
                file=ContentFile(pdf_content, name=f"{self.filename}.pdf")
            )
            vault_file.content_hash = vault_file.create_hash()
            vault_file.save()

            return vault_file

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"LaTeX compilation failed: {e.stderr.decode()}")


class LatexTemplate(models.Model):
    name = models.CharField(max_length=100, unique=True)
    tex_template_file = models.FileField(upload_to='latex_templates/', blank=True)
    json_schema = models.JSONField(blank=True, null=True)
    check_schema = models.BooleanField(default=True)

    def get_template_content(self):
        if self.tex_template_file:
            try:
                self.tex_template_file.seek(0)
                return self.tex_template_file.read().decode('utf-8')
            except Exception:
                return ""
        return ""

    def __str__(self):
        return self.name


class LatexGenerator(models.Model):
    project = models.ForeignKey(LatexProject, on_delete=models.CASCADE, related_name='latex_generators')
    template = models.ForeignKey(LatexTemplate, on_delete=models.PROTECT)
    json_data = models.JSONField()
    filename = models.CharField(max_length=255)

    def clean(self):
        if not self.filename.lower().endswith('.tex'):
            raise ValidationError({'filename': _("Filename must end with .tex")})
        if self.template.check_schema and self.template.json_schema:
            try:
                jsonschema.validate(instance=self.json_data, schema=self.template.json_schema)
            except jsonschema.ValidationError as e:
                raise ValidationError({'json_data': _(str(e))})

    @staticmethod
    def _render_template_from_string(template_text, context=None):
        context = context or {}
        template = Template(template_text)
        return template.render(Context(context))

    def render_to_string(self):
        template_text = self.template.get_template_content()
        return self._render_template_from_string(template_text, self.json_data)

    def bake_to_texfile(self):
        tex_content = self.render_to_string()

        # Define local path for saving .tex file
        output_dir = self.project.get_dir_path()
        output_dir.mkdir(parents=True, exist_ok=True)
        tex_path = output_dir / self.filename

        # Write LaTeX content to local file
        with open(tex_path, 'w', encoding='utf-8') as f:
            f.write(tex_content)

        # Create TexFile instance using local file
        with open(tex_path, 'rb') as f:
            tex_file = TexFile.objects.create(
                project=self.project,
                filename=self.filename,
                file=File(f, name=self.filename)
            )

        return tex_file

