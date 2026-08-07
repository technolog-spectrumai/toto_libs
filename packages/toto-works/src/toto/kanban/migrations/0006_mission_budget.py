"""One budget per mission: an amount and a currency symbol.

Two nullable columns, no data to move, and nothing that can fail on existing
rows — an unbudgeted mission is exactly what every mission is the moment before
this runs. Reversible, unlike 0005: dropping the pair loses the budgets and
nothing else, which is a normal thing to want back out of.

The currency is a CharField and not an FK to ``assets.Asset`` on purpose. That
model ships in toto-economy, which only zenobia pins; studio and aurelian install
kanban from this same wheel and would inherit a migration they cannot build, plus
a package edge ``check_package_graph.py`` forbids. See the field's own comment.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('kanban', '0005_project_wiki'),
    ]

    operations = [
        migrations.AddField(
            model_name='mission',
            name='budget_amount',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='What this mission is budgeted at. Leave empty for no budget.', max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name='mission',
            name='budget_currency',
            field=models.CharField(blank=True, help_text='Currency symbol or code, e.g. ASR, EUR, PLN.', max_length=12),
        ),
    ]
