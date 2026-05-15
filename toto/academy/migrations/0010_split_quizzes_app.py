import django.db.models.deletion
from django.core.management.color import no_style
from django.db import migrations, models


def copy_academy_quizzes(apps, schema_editor):
    OldQuiz = apps.get_model("academy", "Quiz")
    OldQuizQuestion = apps.get_model("academy", "QuizQuestion")
    OldQuizAnswer = apps.get_model("academy", "QuizAnswer")
    OldQuizAnswerTrait = apps.get_model("academy", "QuizAnswerTrait")
    OldQuizAttempt = apps.get_model("academy", "QuizAttempt")
    OldQuizAttemptAnswer = apps.get_model("academy", "QuizAttemptAnswer")
    OldTrait = apps.get_model("academy", "Trait")
    Quiz = apps.get_model("quizzes", "Quiz")
    QuizQuestion = apps.get_model("quizzes", "QuizQuestion")
    QuizAnswer = apps.get_model("quizzes", "QuizAnswer")
    QuizAnswerTrait = apps.get_model("quizzes", "QuizAnswerTrait")
    QuizAttempt = apps.get_model("quizzes", "QuizAttempt")
    QuizAttemptAnswer = apps.get_model("quizzes", "QuizAttemptAnswer")
    QuizTrait = apps.get_model("quizzes", "QuizTrait")

    for trait in OldTrait.objects.all():
        QuizTrait.objects.update_or_create(
            pk=trait.pk,
            defaults={
                "title": trait.title,
                "slug": trait.slug,
                "description": trait.description,
                "icon": trait.icon,
                "order": trait.order,
            },
        )

    for old_quiz in OldQuiz.objects.all():
        quiz, _ = Quiz.objects.update_or_create(
            pk=old_quiz.pk,
            defaults={
                "owner_id": old_quiz.owner_id,
                "title": old_quiz.title,
                "slug": old_quiz.slug,
                "description": old_quiz.description,
                "is_published": old_quiz.is_published,
                "is_official": old_quiz.is_official,
                "order": old_quiz.order,
                "created_at": old_quiz.created_at,
                "updated_at": old_quiz.updated_at,
            },
        )
        if old_quiz.module_id:
            old_quiz.module.attached_quizzes.add(quiz)
        if old_quiz.lesson_id:
            old_quiz.lesson.attached_quizzes.add(quiz)

    for question in OldQuizQuestion.objects.all():
        QuizQuestion.objects.update_or_create(
            pk=question.pk,
            defaults={
                "quiz_id": question.quiz_id,
                "text": question.text,
                "explanation": question.explanation,
                "is_multiple_choice": question.is_multiple_choice,
                "max_time": question.max_time,
                "order": question.order,
            },
        )

    for answer in OldQuizAnswer.objects.all():
        QuizAnswer.objects.update_or_create(
            pk=answer.pk,
            defaults={
                "question_id": answer.question_id,
                "text": answer.text,
                "is_correct": answer.is_correct,
                "explanation": answer.explanation,
                "order": answer.order,
            },
        )

    for mapping in OldQuizAnswerTrait.objects.all():
        QuizAnswerTrait.objects.update_or_create(
            pk=mapping.pk,
            defaults={
                "answer_id": mapping.answer_id,
                "trait_id": mapping.trait_id,
                "weight": mapping.weight,
            },
        )

    for attempt in OldQuizAttempt.objects.select_related("student"):
        QuizAttempt.objects.update_or_create(
            pk=attempt.pk,
            defaults={
                "participant_id": attempt.student.person_id,
                "quiz_id": attempt.quiz_id,
                "started_at": attempt.started_at,
                "completed_at": attempt.completed_at,
            },
        )

    for selection in OldQuizAttemptAnswer.objects.all():
        QuizAttemptAnswer.objects.update_or_create(
            pk=selection.pk,
            defaults={
                "attempt_id": selection.attempt_id,
                "question_id": selection.question_id,
                "answer_id": selection.answer_id,
                "selected_at": selection.selected_at,
            },
        )

    models_to_reset = [
        Quiz,
        QuizQuestion,
        QuizAnswer,
        QuizAnswerTrait,
        QuizAttempt,
        QuizAttemptAnswer,
        QuizTrait,
    ]
    sequence_sql = schema_editor.connection.ops.sequence_reset_sql(no_style(), models_to_reset)
    with schema_editor.connection.cursor() as cursor:
        for sql in sequence_sql:
            cursor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0009_cohort"),
        ("quizzes", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="coursemodule",
            name="attached_quizzes",
            field=models.ManyToManyField(blank=True, help_text="Standalone quizzes attached to this academy module.", related_name="academy_modules", to="quizzes.quiz"),
        ),
        migrations.AddField(
            model_name="lesson",
            name="attached_quizzes",
            field=models.ManyToManyField(blank=True, help_text="Standalone quizzes attached to this academy lesson.", related_name="academy_lessons", to="quizzes.quiz"),
        ),
        migrations.RunPython(copy_academy_quizzes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="certificate",
            name="exam",
            field=models.ForeignKey(blank=True, help_text="Official exam quiz this certificate was awarded for.", limit_choices_to={"is_official": True}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="certificates", to="quizzes.quiz"),
        ),
        migrations.AlterField(
            model_name="coursemodule",
            name="exam",
            field=models.ForeignKey(blank=True, help_text="Official quiz used as the exam for this module.", limit_choices_to={"is_official": True}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="exam_modules", to="quizzes.quiz"),
        ),
        migrations.DeleteModel(name="QuizAttemptAnswer"),
        migrations.DeleteModel(name="QuizAnswerTrait"),
        migrations.DeleteModel(name="QuizAttempt"),
        migrations.DeleteModel(name="QuizAnswer"),
        migrations.DeleteModel(name="QuizQuestion"),
        migrations.DeleteModel(name="Quiz"),
        migrations.DeleteModel(name="Trait"),
    ]
