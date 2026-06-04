from django.contrib.auth import get_user_model

from toto.quizzes.models import Quiz, QuizAnswer, QuizAnswerTrait, QuizQuestion, QuizTrait
from toto.ingress import IngressCommand
from toto.people.models import Person

User = get_user_model()


class Command(IngressCommand):
    help = "Seeds demo quizzes, questions, answers, and quiz traits."

    def process(self):
        if not self.full:
            return

        if Quiz.objects.filter(slug="python-basics-check").exists():
            print("[Ingress] Quiz demo already exists — skipping.")
            return

        owner = self.get_owner()
        traits = self.create_traits()

        quiz_specs = [
            {
                "title": "Python Basics Check",
                "slug": "python-basics-check",
                "description": "A short quiz to assess understanding of core Python concepts.",
                "order": 1,
                "questions": [
                    {
                        "text": "What does `len([1, 2, 3])` return?",
                        "explanation": "`len()` returns the number of items in a sequence. A list with three elements has length 3.",
                        "is_multiple_choice": False,
                        "max_time": None,
                        "answers": [
                            {"text": "2", "is_correct": False, "explanation": "", "traits": []},
                            {"text": "3", "is_correct": True, "explanation": "Correct — the list has three elements.", "traits": [("pragmatist", 1)]},
                            {"text": "4", "is_correct": False, "explanation": "", "traits": []},
                        ],
                    },
                    {
                        "text": "Which of the following are valid ways to create a virtual environment?",
                        "explanation": "Both `python -m venv .venv` and `virtualenv .venv` work. The `-m venv` form is built into Python 3.",
                        "is_multiple_choice": True,
                        "max_time": 3,
                        "answers": [
                            {"text": "python -m venv .venv", "is_correct": True, "explanation": "Built-in Python 3 module.", "traits": [("pragmatist", 2)]},
                            {"text": "virtualenv .venv", "is_correct": True, "explanation": "Third-party tool, also valid.", "traits": [("experimenter", 1)]},
                            {"text": "pip create .venv", "is_correct": False, "explanation": "This command does not exist.", "traits": []},
                            {"text": "python --env .venv", "is_correct": False, "explanation": "Not a valid flag.", "traits": []},
                        ],
                    },
                    {
                        "text": "How do you prefer to learn a new Python concept?",
                        "explanation": "This is a reflection question — no wrong answer. It helps identify your learning style.",
                        "is_multiple_choice": False,
                        "max_time": None,
                        "answers": [
                            {"text": "Read the official docs thoroughly first", "is_correct": False, "explanation": "", "traits": [("theorist", 3)]},
                            {"text": "Jump in and write code immediately", "is_correct": False, "explanation": "", "traits": [("experimenter", 3)]},
                            {"text": "Find a practical project to apply it in", "is_correct": False, "explanation": "", "traits": [("pragmatist", 3)]},
                            {"text": "Work through it with someone else", "is_correct": False, "explanation": "", "traits": [("collaborator", 3)]},
                        ],
                    },
                ],
            },
            {
                "title": "Django Fundamentals Quiz",
                "slug": "django-fundamentals-quiz",
                "description": "Test your knowledge of Django models, views, and URLs.",
                "order": 2,
                "questions": [
                    {
                        "text": "What does `python manage.py migrate` do?",
                        "explanation": "`migrate` applies pending migration files to the database, creating or altering tables to match your current model definitions.",
                        "is_multiple_choice": False,
                        "max_time": None,
                        "answers": [
                            {"text": "Creates new migration files from model changes", "is_correct": False, "explanation": "That is `makemigrations`.", "traits": []},
                            {"text": "Applies pending migrations to the database", "is_correct": True, "explanation": "Correct.", "traits": [("pragmatist", 1)]},
                            {"text": "Restarts the development server", "is_correct": False, "explanation": "", "traits": []},
                        ],
                    },
                    {
                        "text": "Which of these are parts of the MTV pattern in Django?",
                        "explanation": "Django uses Model, Template, and View — its own take on MVC where the view is the request handler and the template is the presentation layer.",
                        "is_multiple_choice": True,
                        "max_time": 2,
                        "answers": [
                            {"text": "Model", "is_correct": True, "explanation": "Handles data and database logic.", "traits": [("theorist", 1)]},
                            {"text": "Template", "is_correct": True, "explanation": "Handles HTML presentation.", "traits": [("theorist", 1)]},
                            {"text": "View", "is_correct": True, "explanation": "Handles request logic.", "traits": [("theorist", 1)]},
                            {"text": "Controller", "is_correct": False, "explanation": "Django uses View instead of Controller.", "traits": []},
                        ],
                    },
                    {
                        "text": "When you encounter a bug in your Django view, what do you do first?",
                        "explanation": "Reflection question — helps identify debugging style.",
                        "is_multiple_choice": False,
                        "max_time": None,
                        "answers": [
                            {"text": "Read the traceback carefully and trace the call stack", "is_correct": False, "explanation": "", "traits": [("theorist", 2)]},
                            {"text": "Add print statements to narrow it down quickly", "is_correct": False, "explanation": "", "traits": [("experimenter", 2)]},
                            {"text": "Search for the error message online", "is_correct": False, "explanation": "", "traits": [("pragmatist", 2)]},
                            {"text": "Ask a colleague to look at it with you", "is_correct": False, "explanation": "", "traits": [("collaborator", 2)]},
                        ],
                    },
                ],
            },
        ]

        for spec in quiz_specs:
            self.create_quiz(spec, owner, traits)

        print("[Ingress] Demo quizzes created successfully.")

    def get_owner(self):
        user, _ = User.objects.get_or_create(
            username="quizzes",
            defaults={
                "email": "quizzes@example.com",
                "is_staff": True,
            },
        )
        person, _ = Person.objects.get_or_create(
            user=user,
            defaults={
                "display_name": "Quiz Owner",
                "email": user.email,
            },
        )
        return person

    def create_traits(self):
        trait_specs = [
            ("Pragmatist", "pragmatist", "Prefers working solutions over theory.", "fa-solid fa-wrench"),
            ("Theorist", "theorist", "Enjoys understanding the underlying concepts deeply.", "fa-solid fa-book"),
            ("Experimenter", "experimenter", "Learns best by trying things out.", "fa-solid fa-flask"),
            ("Collaborator", "collaborator", "Thrives when working with others.", "fa-solid fa-people-group"),
        ]

        traits = {}
        for order, (title, slug, description, icon) in enumerate(trait_specs, start=1):
            trait, _ = QuizTrait.objects.get_or_create(
                slug=slug,
                defaults={
                    "title": title,
                    "description": description,
                    "icon": icon,
                    "order": order,
                },
            )
            traits[slug] = trait
        return traits

    def create_quiz(self, spec, owner, traits):
        quiz = Quiz.objects.create(
            title=spec["title"],
            slug=spec["slug"],
            description=spec["description"],
            owner=owner,
            is_published=True,
            is_official=True,
            order=spec["order"],
        )

        for question_order, question_spec in enumerate(spec["questions"], start=1):
            question = QuizQuestion.objects.create(
                quiz=quiz,
                text=question_spec["text"],
                explanation=question_spec["explanation"],
                is_multiple_choice=question_spec["is_multiple_choice"],
                max_time=question_spec["max_time"],
                order=question_order,
            )

            for answer_order, answer_spec in enumerate(question_spec["answers"], start=1):
                answer = QuizAnswer.objects.create(
                    question=question,
                    text=answer_spec["text"],
                    is_correct=answer_spec["is_correct"],
                    explanation=answer_spec["explanation"],
                    order=answer_order,
                )

                for trait_slug, weight in answer_spec["traits"]:
                    trait = traits.get(trait_slug)
                    if trait:
                        QuizAnswerTrait.objects.create(
                            answer=answer,
                            trait=trait,
                            weight=weight,
                        )

        print(f"[Ingress] Created quiz: {quiz.title} (slug={quiz.slug})")
