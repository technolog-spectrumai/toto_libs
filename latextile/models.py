# projects/models.py
import os
import tempfile
import subprocess
from django.db import models
from django.contrib.auth.models import User
from vault.models import Bucket, VaultFile
from django.core.files.base import ContentFile
import shutil



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

    def _compile(self):
        if not self.file:
            raise ValueError("No LaTeX source file to compile.")

        with tempfile.TemporaryDirectory() as temp_dir:
            tex_path = os.path.join(temp_dir, self.filename)
            shutil.copy2(self.file.path, tex_path)

            subprocess.run(
                ['pdflatex', '-interaction=nonstopmode', '-output-directory', temp_dir, tex_path],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            output_pdf = os.path.join(temp_dir, self.filename.replace('.tex', '.pdf'))
            if os.path.exists(output_pdf):
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

