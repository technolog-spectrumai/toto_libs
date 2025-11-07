# projects/models.py
import os.path
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
import logging
from django.utils.timezone import now

logger = logging.getLogger(__name__)



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
        logger.info(f"Compiling all LaTeX files for project '{self.name}' (ID: {self.id})")
        compiled = []
        for texfile in self.tex_files.all():
            try:
                result = texfile.compile()
                if result:
                    compiled.append(result)
                    logger.info(f"Compiled '{texfile.filename}' successfully.")
            except Exception as e:
                logger.warning(f"Failed to compile '{texfile.filename}': {e}")
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
            logger.info(f"Cleaned output directory for project '{self.name}' at '{output_dir}'")

    def __str__(self):
        return self.name


class LatexCompilationProcess(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    log = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    tex_file = models.FileField(upload_to='latex_compilation/tex/', blank=True, null=True)
    aux_file = models.FileField(upload_to='latex_compilation/aux/', blank=True, null=True)
    pdf_file = models.FileField(upload_to='latex_compilation/pdf/', blank=True, null=True)
    output_dir = models.CharField(max_length=512, blank=True, null=True)

    def __str__(self):
        return f"Compilation for {self.tex_file.name if self.tex_file else 'unspecified'} [{self.status}]"

    @staticmethod
    def run_pdflatex(output_dir, tex_path):
        subprocess.run(
            ['pdflatex', '-interaction=nonstopmode', '-output-directory', output_dir, tex_path],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

    def run_compile(self):
        log = []
        self.status = 'running'
        self.started_at = now()
        self.save()
        pdf_path = None

        try:
            # Prepare output directory
            output_dir = Path(self.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Prepare .tex file
            tex_path = output_dir / 'main.tex'
            with open(tex_path, 'wb') as f:
                f.write(self.tex_file.read())
            log.append(f"Copied source file to '{tex_path}'")

            # Run pdflatex (first pass)
            self.run_pdflatex(str(output_dir), str(tex_path))
            log.append("First pdflatex run completed.")

            # Check for .aux file
            aux_path = output_dir / 'main.aux'
            if not aux_path.exists():
                self.run_pdflatex(str(output_dir), str(tex_path))
                log.append("Second pdflatex run completed (aux file was missing).")

            # Check for PDF output
            pdf_path = output_dir / 'main.pdf'
            if pdf_path.exists():
                log.append(f"PDF successfully generated at '{pdf_path}'")

                # Save aux file
                if aux_path.exists():
                    with open(aux_path, 'rb') as f:
                        self.aux_file.save(aux_path.name, File(f), save=False)

                # Save pdf file
                with open(pdf_path, 'rb') as f:
                    self.pdf_file.save(pdf_path.name, File(f), save=False)

                self.status = 'success'
            else:
                log.append("PDF not found after compilation.")
                self.status = 'failed'

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode()
            log.append(f"LaTeX compilation failed: {error_msg}")
            self.status = 'failed'

        self.log = "\n".join(log)
        self.finished_at = now()
        self.save()

        return pdf_path


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

    def _create_compilation(self, tex_path: Path, aux_path: Path, pdf_path: Path,
                              output_dir: Path) -> LatexCompilationProcess:
        process = LatexCompilationProcess.objects.create(
            output_dir=str(output_dir),
            status='pending'
        )

        # Save .tex file
        if tex_path.exists():
            with open(tex_path, 'rb') as f:
                process.tex_file.save(tex_path.name, File(f), save=False)

        # Save .aux file
        if aux_path.exists():
            with open(aux_path, 'rb') as f:
                process.aux_file.save(aux_path.name, File(f), save=False)

        # Save .pdf file
        if pdf_path.exists():
            with open(pdf_path, 'rb') as f:
                process.pdf_file.save(pdf_path.name, File(f), save=False)

        process.save()
        return process

    def compile(self):
        key = self.project.get_key()
        output_dir = self.project.get_dir_path()
        output_dir.mkdir(parents=True, exist_ok=True)

        tex_path = output_dir / f"{key}.tex"
        source_path = Path(str(self.file.path))

        if not source_path.exists():
            raise RuntimeError(f"LaTeX source {source_path} does not exist.")

        try:
            shutil.copy2(source_path, tex_path)
        except Exception as e:
            logger.error(f"Failed to copy file: {e}")
            print(f"Failed to copy file: {e}")
            raise

        aux_path = output_dir / f"{key}.aux"
        pdf_path = output_dir / self.filename.replace('.tex', '.pdf')

        process = self._create_compilation(tex_path, aux_path, pdf_path, output_dir)
        pdf_path = process.run_compile()
        if not pdf_path:
            raise RuntimeError("PDF file was not generated.")

        vault_file = VaultFile.objects.create(
            owner=self.project.user,
            title=f"{self.filename} (compiled)",
            file_type='pdf',
            bucket=self.project.bucket,
            file=File(open(pdf_path, 'rb'), name=f"{self.filename}.pdf")
        )
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save()
        logger.debug(f"VaultFile created for '{self.filename}' as '{vault_file.title}' (ID: {vault_file.id})")
        return vault_file


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
            logger.info(f"Baked LaTeX content to '{tex_path}'")

        # Create TexFile instance using local file
        with open(tex_path, 'rb') as f:
            tex_file = TexFile.objects.create(
                project=self.project,
                filename=self.filename,
                file=File(f, name=self.filename)
            )
        logger.info(f"TexFile created: '{tex_file.filename}' for project '{self.project.name}'")
        return tex_file

