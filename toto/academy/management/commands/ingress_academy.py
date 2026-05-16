from django.contrib.auth.models import User
from django.utils.text import slugify

from toto.competence.models import SkillBadge
from toto.memo.models import MemoDeck
from toto.quizzes.models import Quiz
from toto.ingress import IngressCommand
from toto.people.models import Person

from toto.academy.models import (
    Certificate,
    Course,
    CourseEnrollment,
    CourseModule,
    Lesson,
    Script,
    ScriptSection,
    Student,
    StudentBadge,
    Teacher,
)


class Command(IngressCommand):
    help = "Creates a simple demo academy setup"

    def process(self):
        if not self.full:
            return

        author, _ = User.objects.get_or_create(
            username="academy",
            defaults={
                "email": "academy@example.com",
                "is_staff": True,
            },
        )

        # ----------------------------------------------------
        # Teachers
        # ----------------------------------------------------
        teacher_specs = [
            {
                "username": "teacher.ada",
                "display_name": "Ada Novak",
                "email": "ada.novak@example.com",
                "title": "Senior Python Teacher",
                "bio": "Builds calm, practical paths from first syntax to working backend systems.",
            },
            {
                "username": "teacher.marek",
                "display_name": "Marek Zielinski",
                "email": "marek.zielinski@example.com",
                "title": "Django Workshop Mentor",
                "bio": "Focuses on readable architecture, models, and practical web workflows.",
            },
        ]

        teachers = []

        for spec in teacher_specs:
            user, _ = User.objects.get_or_create(
                username=spec["username"],
                defaults={
                    "email": spec["email"],
                },
            )

            person, _ = Person.objects.get_or_create(
                user=user,
                defaults={
                    "display_name": spec["display_name"],
                    "email": spec["email"],
                    "bio": spec["bio"],
                },
            )

            person.display_name = spec["display_name"]
            person.email = spec["email"]
            person.bio = spec["bio"]
            person.save()

            teacher, _ = Teacher.objects.get_or_create(
                person=person,
                defaults={
                    "title": spec["title"],
                    "bio": spec["bio"],
                },
            )
            teacher.title = spec["title"]
            teacher.bio = spec["bio"]
            teacher.save()

            teachers.append(teacher)

        # Dean — the admin user
        try:
            admin_user = User.objects.get(username="admin")
            admin_person, _ = Person.objects.get_or_create(
                user=admin_user,
                defaults={
                    "display_name": admin_user.get_full_name() or admin_user.username,
                    "email": admin_user.email,
                },
            )
            dean, _ = Teacher.objects.get_or_create(
                person=admin_person,
                defaults={"title": "Dean", "bio": "Head of the academy."},
            )
            dean.title = "Dean"
            dean.save()
            print("✔ Admin user registered as Dean.")
        except User.DoesNotExist:
            dean = None
            print("⚠ No 'admin' user found — skipping Dean.")

        # ----------------------------------------------------
        # Memo decks
        # ----------------------------------------------------
        decks = []

        for i in range(1, 7):
            deck, _ = MemoDeck.objects.get_or_create(
                slug=f"academy-demo-lecture-{i}",
                defaults={
                    "title": f"Academy Demo Lecture {i}",
                    "description": "Demo lecture deck for academy.",
                    "author": author,
                },
            )
            decks.append(deck)

        # ----------------------------------------------------
        # Skill badges from toto.competence
        # ----------------------------------------------------
        badge_slugs = ["python-basics", "django-basics", "api-basics"]
        badges = list(
            SkillBadge.objects
            .filter(group__slug="python-developer", slug__in=badge_slugs)
            .select_related("group")
            .order_by("order")
        )
        if len(badges) < len(badge_slugs):
            print("⚠ Competence skill tree is missing. Run ingress_competence first.")
            return

        # ----------------------------------------------------
        # Course
        # ----------------------------------------------------
        course, _ = Course.objects.get_or_create(
            slug="python-backend-basics",
            defaults={
                "title": "Python Backend Basics",
                "description": "A short demo course built around the Python Developer skill tree.",
                "author": author,
                "owner": teachers[0],
                "is_published": True,
                "order": 1,
            },
        )
        course.owner = teachers[0]
        course.is_virtual = False
        course.save()

        virtual_course, _ = Course.objects.get_or_create(
            slug="backend-teaching-portfolio",
            defaults={
                "title": "Backend Teaching Portfolio",
                "description": "A virtual curriculum item used on teacher profiles.",
                "author": author,
                "owner": teachers[0],
                "is_published": False,
                "is_virtual": True,
                "order": 99,
            },
        )
        virtual_course.owner = teachers[0]
        virtual_course.is_virtual = True
        virtual_course.save()

        module_data = [
            (badges[0], "Python Fundamentals", "Variables, conditions, loops, and functions.", teachers[0]),
            (badges[1], "Django Fundamentals", "Django models, views, templates, and routing.", teachers[1]),
            (badges[2], "API Fundamentals", "Simple API design and backend integration.", teachers[0]),
        ]

        for module_order, (badge, title, description, teacher) in enumerate(module_data, start=1):
            module, _ = CourseModule.objects.get_or_create(
                course=course,
                slug=slugify(title),
                defaults={
                    "title": title,
                    "description": description,
                    "unlocks_badge": badge,
                    "owner": teacher,
                    "order": module_order,
                },
            )
            module.owner = teacher
            module.save()

            for lesson_order in range(1, 3):
                lesson_title = f"{title} Lesson {lesson_order}"

                lesson, _ = Lesson.objects.get_or_create(
                    module=module,
                    slug=slugify(lesson_title),
                    defaults={
                        "title": lesson_title,
                        "summary": f"Demo lesson for {title.lower()}.",
                        "owner": teacher,
                        "order": lesson_order,
                        "lecture": decks[((module_order - 1) * 2) + lesson_order - 1],
                    },
                )
                lesson.owner = teacher
                lesson.save()

        Certificate.objects.get_or_create(
            person=teachers[0].person,
            course=course,
            defaults={
                "title": "Python Backend Teaching Certificate",
                "description": "Demo teaching credential linked to the backend basics course.",
            },
        )
        Certificate.objects.get_or_create(
            person=teachers[1].person,
            title="Django Mentorship Credential",
            defaults={
                "description": "Demo standalone teaching credential.",
            },
        )

        # ----------------------------------------------------
        # Optional student
        # ----------------------------------------------------
        person = Person.objects.first()

        if person:
            student, _ = Student.objects.get_or_create(person=person)

            CourseEnrollment.objects.get_or_create(
                student=student,
                course=course,
            )

            StudentBadge.objects.get_or_create(
                student=student,
                badge=badges[0],
            )

            print("✔ Created demo student enrollment.")

        # ----------------------------------------------------
        # 📜 Scripts
        # ----------------------------------------------------
        script_data = [
            {
                "module_slug": "python-fundamentals",
                "title": "Python Setup Guide",
                "slug": "python-setup-guide",
                "description": "How to install Python, configure a virtual environment, and run your first script.",
                "sections": [
                    {
                        "title": "Installing Python",
                        "content": "<h2>Installing Python</h2><p>Download the latest Python 3 release from <strong>python.org</strong>. On Linux, prefer your package manager: <code>sudo apt install python3 python3-venv</code>. Verify with <code>python3 --version</code>.</p>",
                        "order": 1,
                    },
                    {
                        "title": "Virtual Environments",
                        "content": "<h2>Virtual Environments</h2><p>Create an isolated environment with <code>python3 -m venv .venv</code> and activate it: <code>source .venv/bin/activate</code> (Linux/macOS) or <code>.venv\\Scripts\\activate</code> (Windows). Install packages inside without affecting the system Python.</p>",
                        "order": 2,
                    },
                    {
                        "title": "Running Your First Script",
                        "content": "<h2>Running Your First Script</h2><p>Create <code>hello.py</code> with <code>print(\"Hello, world!\")</code>. Run it with <code>python hello.py</code>. If you see the output, your environment is working correctly.</p>",
                        "order": 3,
                    },
                ],
            },
            {
                "module_slug": "django-fundamentals",
                "title": "Django Project Checklist",
                "slug": "django-project-checklist",
                "description": "Step-by-step checklist for starting a new Django project from scratch.",
                "sections": [
                    {
                        "title": "Create the Project",
                        "content": "<h2>Create the Project</h2><p>Run <code>django-admin startproject myproject</code> then <code>cd myproject</code>. Open <code>settings.py</code> and set <code>ALLOWED_HOSTS</code>, <code>DATABASES</code>, and <code>INSTALLED_APPS</code> before doing anything else.</p>",
                        "order": 1,
                    },
                    {
                        "title": "First App",
                        "content": "<h2>First App</h2><p>Run <code>python manage.py startapp core</code> and register it in <code>INSTALLED_APPS</code>. Define a model, run <code>makemigrations</code> and <code>migrate</code>, then wire a view to a URL to confirm everything connects.</p>",
                        "order": 2,
                    },
                ],
            },
        ]

        for spec in script_data:
            try:
                module = CourseModule.objects.get(course=course, slug=spec["module_slug"])
            except CourseModule.DoesNotExist:
                print(f"⚠ Module '{spec['module_slug']}' not found, skipping script.")
                continue

            script, _ = Script.objects.get_or_create(
                slug=spec["slug"],
                defaults={
                    "title": spec["title"],
                    "description": spec["description"],
                    "module": module,
                    "author": teachers[0].person,
                },
            )

            for sec in spec["sections"]:
                ScriptSection.objects.get_or_create(
                    page=script,
                    title=sec["title"],
                    defaults={
                        "content": sec["content"],
                        "order": sec["order"],
                    },
                )

        # ----------------------------------------------------
        # ❓ Attach standalone quizzes from toto.quizzes
        # ----------------------------------------------------
        quiz_links = {
            "python-fundamentals": "python-basics-check",
            "django-fundamentals": "django-fundamentals-quiz",
        }

        for module_slug, quiz_slug in quiz_links.items():
            try:
                module = CourseModule.objects.get(course=course, slug=module_slug)
                quiz = Quiz.objects.get(slug=quiz_slug)
            except CourseModule.DoesNotExist:
                print(f"⚠ Module '{module_slug}' not found, skipping quiz link.")
                continue
            except Quiz.DoesNotExist:
                print(f"⚠ Quiz '{quiz_slug}' not found, skipping academy link. Run ingress_quizzes first.")
                continue

            module.attached_quizzes.add(quiz)
            if quiz.is_official and module.exam_id is None:
                module.exam = quiz
                module.save(update_fields=["exam"])

        print("[Ingress] Simple Academy demo created successfully.")
