"""Bring back the 'presentation' file type.

Choices are not DB-enforced, so this changes no data and no column — but leaving
the model and the migration state out of step makes `makemigrations --check`
fail for every host. See the comment on VaultFile.FILE_TYPES for why the type
came back while the `.pml` extension stayed retired.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vault', '0010_alter_vaultfile_file_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='vaultfile',
            name='file_type',
            field=models.CharField(choices=[('pdf', 'PDF'), ('image', 'Image'), ('html', 'HTML'), ('text', 'Text File'), ('json', 'JSON'), ('yaml', 'YAML'), ('xml', 'XML'), ('latex', 'LaTeX'), ('bib', 'Bibliography'), ('csv', 'CSV'), ('svg', 'SVG File'), ('audio', 'Audio'), ('video', 'Video'), ('python', 'Python'), ('neojson', 'NeoJSON'), ('sheet', 'Primula Sheet'), ('presentation', 'Presentation'), ('zip', 'Archive')], max_length=16),
        ),
    ]
