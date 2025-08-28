from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from kanban.models import Project, Board, Column, Task, Sprint

class Command(BaseCommand):
    help = 'Creates a fake kanban setup with a current sprint'

    def handle(self, *args, **kwargs):
        # Create or get a user
        user, _ = User.objects.get_or_create(username='demo_user', defaults={'email': 'demo@example.com'})

        # Create a project
        project = Project.objects.create(
            name='Demo Project',
            description='A sample project for kanban testing',
            owner=user
        )
        project.collaborators.add(user)

        # Create a board
        board = Board.objects.create(
            name='Development Board',
            project=project
        )

        # Create columns
        todo = Column.objects.create(name='To Do', board=board, position=1)
        doing = Column.objects.create(name='In Progress', board=board, position=2)
        done = Column.objects.create(name='Done', board=board, position=3)

        # Create tasks
        tasks = []
        for i in range(1, 6):
            task = Task.objects.create(
                column=todo if i < 3 else doing,
                title=f'Task {i}',
                description='This is a sample task.',
                assignee=user,
                due_date=timezone.now().date() + timedelta(days=i),
                completed=False,
                position=i,
                story_points=i % 3 + 1
            )
            tasks.append(task)

        # Create a current sprint
        sprint = Sprint.objects.create(
            name='Sprint Alpha',
            project=project,
            start=timezone.now(),
            end=timezone.now() + timedelta(days=14)
        )
        sprint.tasks.set(tasks)

        self.stdout.write(self.style.SUCCESS('✅ Fake kanban data created successfully!'))
