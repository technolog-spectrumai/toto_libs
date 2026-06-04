from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('treasury', '0001_initial'),
        ('claims', '0001_initial'),
        ('contracts', '0004_contract_body_vault_pdf'),
        ('people', '0003_add_digital_signature'),
        ('assets', '0007_remove_asset_mint_transaction_type'),
    ]

    operations = [
        migrations.DeleteModel(name='BudgetItem'),
        migrations.DeleteModel(name='BudgetLedgerAccount'),
        migrations.DeleteModel(name='Budget'),
        migrations.DeleteModel(name='BudgetStreamType'),
    ]
