from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('mobilization', '0006_intervention_type_model'),
        ('people', '0003_person_is_federal_agent'),
    ]

    operations = [
        migrations.CreateModel(
            name='AchievementBadge',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, unique=True)),
                ('slug', models.SlugField(max_length=200, unique=True)),
                ('description', models.TextField(blank=True)),
                ('icon', models.CharField(default='fa-solid fa-medal', max_length=100)),
                ('category', models.CharField(
                    choices=[
                        ('service', 'Service'),
                        ('rescue', 'Rescue'),
                        ('leadership', 'Leadership'),
                        ('training', 'Training'),
                        ('special', 'Special'),
                    ],
                    default='service',
                    max_length=30,
                )),
                ('order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'ordering': ['category', 'order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='PersonAchievement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('awarded_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('note', models.TextField(blank=True)),
                ('badge', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='awarded_to',
                    to='mobilization.achievementbadge',
                )),
                ('deployment', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='achievement_awards',
                    to='mobilization.deployment',
                )),
                ('awarded_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='awarded_achievements',
                    to='people.person',
                )),
                ('person', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='mobilization_achievements',
                    to='people.person',
                )),
            ],
            options={
                'ordering': ['-awarded_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='personachievement',
            constraint=models.UniqueConstraint(
                fields=['person', 'badge'],
                name='unique_person_achievement',
            ),
        ),
    ]
