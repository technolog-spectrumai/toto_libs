from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Drop LatexWorkspace and LatexFile; replace CompileRun.workspace /
    CompileRun.latex_file with a direct FK to vault.VaultFile.
    """

    dependencies = [
        ("texlab", "0001_initial"),
        ("vault", "0001_initial"),
    ]

    operations = [
        # 1. Add vault_file FK to CompileRun (nullable while old FKs exist)
        migrations.AddField(
            model_name="compilerun",
            name="vault_file",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="texlab_compile_runs",
                to="vault.vaultfile",
            ),
        ),

        # 2. Drop old FKs from CompileRun
        migrations.RemoveField(model_name="compilerun", name="workspace"),
        migrations.RemoveField(model_name="compilerun", name="latex_file"),

        # 3. Make vault_file non-nullable now that workspace is gone
        migrations.AlterField(
            model_name="compilerun",
            name="vault_file",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="texlab_compile_runs",
                to="vault.vaultfile",
            ),
        ),

        # 4. Drop LatexFile first (FK into LatexWorkspace)
        migrations.DeleteModel("LatexFile"),

        # 5. Drop LatexWorkspace
        migrations.DeleteModel("LatexWorkspace"),
    ]
