from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from kanban.models import Project, Board, Column, Task, Sprint, ColorMix

class Command(BaseCommand):
    help = 'Creates a demo kanban setup with a current sprint using ColorMix themes'

    def handle(self, *args, **kwargs):
        # Create or get demo user
        user, _ = User.objects.get_or_create(
            username='demo_user',
            defaults={'email': 'demo@example.com'}
        )

        # Create project
        project = Project.objects.create(
            name='Demo Project',
            description='A sample project for kanban testing',
            owner=user
        )
        project.collaborators.add(user)

        # Create board
        board = Board.objects.create(
            name='Development Board',
            project=project
        )

        # Create or get color themes
        blue_theme, _ = ColorMix.objects.get_or_create(
            name='Blue Theme',
            defaults={
                # Light mode: very pale, muted sky-blue
                'bg_color_light': '#E0F2FE',
                'text_color_light': '#1E3A8A',
                # Dark mode: deep, muted navy
                'bg_color_dark': '#0F172A',
                'text_color_dark': '#E2E8F0',
            }
        )

        yellow_theme, _ = ColorMix.objects.get_or_create(
            name='Yellow Theme',
            defaults={
                # Light mode: pale, muted butter-yellow
                'bg_color_light': '#FEF3C7',
                'text_color_light': '#92400E',
                # Dark mode: dark, muted ochre
                'bg_color_dark': '#332F1C',
                'text_color_dark': '#F1E9D6',
            }
        )

        green_theme, _ = ColorMix.objects.get_or_create(
            name='Green Theme',
            defaults={
                # Light mode: pale mint-green
                'bg_color_light': '#DCFCE7',
                'text_color_light': '#14532D',
                # Dark mode: nearly black with green undertone
                'bg_color_dark': '#04200A',
                'text_color_dark': '#D1FAE5',
            }
        )
        # Create columns with color_mix
        todo = Column.objects.create(
            name='To Do',
            board=board,
            position=1,
            color_mix=blue_theme
        )
        doing = Column.objects.create(
            name='In Progress',
            board=board,
            position=2,
            color_mix=yellow_theme
        )
        done = Column.objects.create(
            name='Done',
            board=board,
            position=3,
            color_mix=green_theme
        )

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

        self.stdout.write(self.style.SUCCESS('Demo kanban setup with ColorMix themes created successfully!'))
