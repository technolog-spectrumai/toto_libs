from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('anastasia', '0011_installrun'),
    ]

    operations = [
        migrations.AddField(
            model_name='installrun',
            name='kind',
            field=models.CharField(choices=[('python', 'Python'), ('latex', 'LaTeX')], default='python', max_length=12),
        ),
    ]
