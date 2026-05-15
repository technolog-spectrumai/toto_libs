import django.db.models.deletion
from django.core.management.color import no_style
from django.db import migrations, models


def copy_competence_data(apps, schema_editor):
    OldExperience = apps.get_model("academy", "Experience")
    OldSkillGroup = apps.get_model("academy", "SkillGroup")
    OldSkillBadge = apps.get_model("academy", "SkillBadge")
    OldSkillBadgePrerequisite = apps.get_model("academy", "SkillBadgePrerequisite")
    Experience = apps.get_model("competence", "Experience")
    SkillGroup = apps.get_model("competence", "SkillGroup")
    SkillBadge = apps.get_model("competence", "SkillBadge")
    SkillBadgePrerequisite = apps.get_model("competence", "SkillBadgePrerequisite")

    for experience in OldExperience.objects.all():
        Experience.objects.update_or_create(
            pk=experience.pk,
            defaults={
                "person_id": experience.person_id,
                "title": experience.title,
                "institution": experience.institution,
                "place": experience.place,
                "started_at": experience.started_at,
                "ended_at": experience.ended_at,
                "is_current": experience.is_current,
                "description": experience.description,
                "order": experience.order,
            },
        )

    for group in OldSkillGroup.objects.all():
        SkillGroup.objects.update_or_create(
            pk=group.pk,
            defaults={
                "title": group.title,
                "slug": group.slug,
                "description": group.description,
                "order": group.order,
            },
        )

    for badge in OldSkillBadge.objects.all():
        SkillBadge.objects.update_or_create(
            pk=badge.pk,
            defaults={
                "group_id": badge.group_id,
                "title": badge.title,
                "slug": badge.slug,
                "description": badge.description,
                "icon": badge.icon,
                "order": badge.order,
            },
        )

    for prerequisite in OldSkillBadgePrerequisite.objects.all():
        SkillBadgePrerequisite.objects.update_or_create(
            pk=prerequisite.pk,
            defaults={
                "badge_id": prerequisite.badge_id,
                "prerequisite_id": prerequisite.prerequisite_id,
            },
        )

    models_to_reset = [Experience, SkillGroup, SkillBadge, SkillBadgePrerequisite]
    sequence_sql = schema_editor.connection.ops.sequence_reset_sql(no_style(), models_to_reset)
    with schema_editor.connection.cursor() as cursor:
        for sql in sequence_sql:
            cursor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0010_split_quizzes_app"),
        ("competence", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(copy_competence_data, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="coursemodule",
            name="unlocks_badge",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="unlocking_modules", to="competence.skillbadge"),
        ),
        migrations.AlterField(
            model_name="learningpath",
            name="badges",
            field=models.ManyToManyField(blank=True, related_name="learning_paths", through="academy.LearningPathBadge", to="competence.skillbadge"),
        ),
        migrations.AlterField(
            model_name="learningpathbadge",
            name="badge",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="path_steps", to="competence.skillbadge"),
        ),
        migrations.AlterField(
            model_name="student",
            name="badges",
            field=models.ManyToManyField(blank=True, related_name="students", through="academy.StudentBadge", to="competence.skillbadge"),
        ),
        migrations.AlterField(
            model_name="studentbadge",
            name="badge",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="earned_by_students", to="competence.skillbadge"),
        ),
        migrations.DeleteModel(name="Experience"),
        migrations.DeleteModel(name="SkillBadgePrerequisite"),
        migrations.DeleteModel(name="SkillBadge"),
        migrations.DeleteModel(name="SkillGroup"),
    ]
