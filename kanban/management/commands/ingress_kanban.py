from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.models import User
from kanban.models import Project, Board, Column, Task, Sprint, ColorMix
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Creates a demo kanban setup with a current sprint using ColorMix themes"

    def process(self, _):
        self.create_dashboard_item(
            title="Kanban Demo",
            icon="clipboard-list",
            description="A demo kanban board with tasks, columns, and sprint setup.",
            link="/kanban/"
        )

        # Create demo user
        user, _ = User.objects.get_or_create(
            username='demo_user',
            defaults={'email': 'demo@example.com'}
        )

        # Create project and board
        project = Project.objects.create(
            name='Demo Project',
            description='A sample project for kanban testing',
            owner=user
        )
        project.collaborators.add(user)

        board = Board.objects.create(
            name='Development Board',
            project=project
        )

        # Define color themes
        themes = {
            'Blue Theme': {
                'bg_color_light': '#E0F2FE',
                'text_color_light': '#1E3A8A',
                'bg_color_dark': '#0F172A',
                'text_color_dark': '#E2E8F0',
            },
            'Yellow Theme': {
                'bg_color_light': '#FEF3C7',
                'text_color_light': '#92400E',
                'bg_color_dark': '#332F1C',
                'text_color_dark': '#F1E9D6',
            },
            'Green Theme': {
                'bg_color_light': '#DCFCE7',
                'text_color_light': '#14532D',
                'bg_color_dark': '#04200A',
                'text_color_dark': '#D1FAE5',
            }
        }

        # Create color mixes
        mixes = {}
        for name, colors in themes.items():
            mix, _ = ColorMix.objects.get_or_create(name=name, defaults=colors)
            mixes[name] = mix

        # Create columns
        todo = Column.objects.create(name='To Do', board=board, position=1, color_mix=mixes['Blue Theme'])
        doing = Column.objects.create(name='In Progress', board=board, position=2, color_mix=mixes['Yellow Theme'])
        done = Column.objects.create(name='Done', board=board, position=3, color_mix=mixes['Green Theme'])

        # Create tasks
        tasks = []
        for i in range(1, 6):
            column = todo if i < 3 else doing
            task = Task.objects.create(
                column=column,
                title=f'Task {i}',
                description='This is a sample task.',
                assignee=user,
                due_date=timezone.now().date() + timedelta(days=i),
                position=i
            )
            tasks.append(task)

        # Create sprint
        sprint = Sprint.objects.create(
            name='Sprint Alpha',
            project=project,
            start=timezone.now(),
            end=timezone.now() + timedelta(days=14)
        )
        sprint.tasks.set(tasks)

        self.stdout.write(self.style.SUCCESS("Demo kanban setup with ColorMix themes created successfully!"))
