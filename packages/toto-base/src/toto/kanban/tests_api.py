import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.kanban.models import Project, Column, Task, Campaign, Mission, Practitioner, ProjectCommitment
from toto.people.models import Person
from toto.api.testutils import add_to_mesh

User = get_user_model()


def _make_project_with_columns(lead_person, col_names=("Todo", "In Progress", "Done")):
    project = Project.objects.create(name="Test Project", project_lead=lead_person)
    columns = []
    for i, name in enumerate(col_names):
        columns.append(Column.objects.create(project=project, name=name, position=i))
    return project, columns


def _make_task(project, column, title="Task"):
    campaign = Campaign.objects.filter(project=project).first()
    if not campaign:
        campaign = Campaign.objects.create(project=project, name="C")
    mission = Mission.objects.filter(campaign=campaign).first()
    if not mission:
        mission = Mission.objects.create(campaign=campaign, title="M")
    return Task.objects.create(title=title, column=column, mission=mission)


class ProjectListApiTests(TestCase):
    def setUp(self):
        self.user = add_to_mesh(User.objects.create_user(username="kanbanuser", password="pass"))
        self.other = User.objects.create_user(username="kanbanother", password="pass")
        self.person = Person.objects.create(user=self.user, display_name="Kanban User", email="k@x.com")
        self.other_person = Person.objects.create(user=self.other, display_name="Other", email="o@x.com")

    def test_list_unauthenticated(self):
        res = self.client.get("/kanban/api/projects/")
        self.assertEqual(res.status_code, 401)

    def test_list_own_projects(self):
        _make_project_with_columns(self.person)
        _make_project_with_columns(self.other_person)
        self.client.force_login(self.user)
        res = self.client.get("/kanban/api/projects/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data["projects"]), 1)

    def test_list_committed_project_included(self):
        project, _ = _make_project_with_columns(self.other_person)
        practitioner = Practitioner.objects.create(person=self.person, role="contributor")
        ProjectCommitment.objects.create(practitioner=practitioner, project=project, hours_per_day=4)
        self.client.force_login(self.user)
        res = self.client.get("/kanban/api/projects/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["projects"]), 1)


class ProjectDetailApiTests(TestCase):
    def setUp(self):
        self.user = add_to_mesh(User.objects.create_user(username="projdet", password="pass"))
        self.person = Person.objects.create(user=self.user, display_name="Det", email="det@x.com")
        self.project, self.columns = _make_project_with_columns(self.person)

    def test_detail_unauthenticated(self):
        res = self.client.get(f"/kanban/api/projects/{self.project.pk}/")
        self.assertEqual(res.status_code, 401)

    def test_detail_returns_columns(self):
        self.client.force_login(self.user)
        res = self.client.get(f"/kanban/api/projects/{self.project.pk}/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["name"], "Test Project")
        self.assertEqual(len(data["columns"]), 3)

    def test_detail_not_found(self):
        self.client.force_login(self.user)
        res = self.client.get("/kanban/api/projects/99999/")
        self.assertEqual(res.status_code, 404)


class TaskListCreateApiTests(TestCase):
    def setUp(self):
        self.user = add_to_mesh(User.objects.create_user(username="tasker", password="pass"))
        self.person = Person.objects.create(user=self.user, display_name="Tasker", email="t@x.com")
        self.project, self.columns = _make_project_with_columns(self.person)
        self.col = self.columns[0]

    def test_list_unauthenticated(self):
        res = self.client.get(f"/kanban/api/projects/{self.project.pk}/tasks/")
        self.assertEqual(res.status_code, 401)

    def test_list_tasks_for_project(self):
        _make_task(self.project, self.col, "My Task")
        self.client.force_login(self.user)
        res = self.client.get(f"/kanban/api/projects/{self.project.pk}/tasks/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["tasks"]), 1)

    def test_create_unauthenticated(self):
        res = self.client.post(
            f"/kanban/api/projects/{self.project.pk}/tasks/",
            json.dumps({"title": "New", "column_id": self.col.pk}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)

    def test_create_task(self):
        self.client.force_login(self.user)
        res = self.client.post(
            f"/kanban/api/projects/{self.project.pk}/tasks/",
            json.dumps({"title": "New task", "column_id": self.col.pk}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["title"], "New task")
        self.assertEqual(data["column_id"], self.col.pk)

    def test_create_task_missing_title(self):
        self.client.force_login(self.user)
        res = self.client.post(
            f"/kanban/api/projects/{self.project.pk}/tasks/",
            json.dumps({"column_id": self.col.pk}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)

    def test_create_task_invalid_column(self):
        self.client.force_login(self.user)
        res = self.client.post(
            f"/kanban/api/projects/{self.project.pk}/tasks/",
            json.dumps({"title": "X", "column_id": 9999}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 404)


class TaskPromoteDemoteApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mover", password="pass")
        self.person = Person.objects.create(user=self.user, display_name="Mover", email="m@x.com")
        self.project, self.columns = _make_project_with_columns(self.person, ("Todo", "Doing", "Done"))
        self.task = _make_task(self.project, self.columns[0], "Move me")

    def test_promote_moves_to_next_column(self):
        self.client.force_login(self.user)
        res = self.client.post(f"/kanban/api/tasks/{self.task.pk}/promote/")
        self.assertEqual(res.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.column, self.columns[1])

    def test_promote_past_last_column(self):
        self.task.column = self.columns[2]
        self.task.save()
        self.client.force_login(self.user)
        res = self.client.post(f"/kanban/api/tasks/{self.task.pk}/promote/")
        self.assertEqual(res.status_code, 400)

    def test_demote_moves_to_previous_column(self):
        self.task.column = self.columns[1]
        self.task.save()
        self.client.force_login(self.user)
        res = self.client.post(f"/kanban/api/tasks/{self.task.pk}/demote/")
        self.assertEqual(res.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.column, self.columns[0])

    def test_demote_before_first_column(self):
        self.client.force_login(self.user)
        res = self.client.post(f"/kanban/api/tasks/{self.task.pk}/demote/")
        self.assertEqual(res.status_code, 400)

    def test_promote_unauthenticated(self):
        res = self.client.post(f"/kanban/api/tasks/{self.task.pk}/promote/")
        self.assertEqual(res.status_code, 401)
