"""The completed_at invariant, and task relations.

``completed_at`` used to be maintained only by the HTML promote and demote
views. The JSON API's promote, demote and patch never touched it, so a task
moved through the API was done on the board and open in every chart. These
tests put all five write paths in one class, because what matters is that they
*agree* — split by transport, the disagreement is invisible.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.kanban.metrics import SprintMetricsCalculator
from toto.kanban.models import (
    Campaign, Mission, Practitioner, Project, ProjectCommitment, RelationType,
    Task, TaskRelation, TaskStatus, adjacent_status,
)
from toto.people.models import Person

User = get_user_model()


def _make_world(username="invariant"):
    user = add_to_mesh(User.objects.create_user(username=username, password="pass"))
    person = Person.objects.create(user=user, display_name="Mover", email=f"{username}@x.com")
    practitioner = Practitioner.objects.create(person=person, role="manager")
    project = Project.objects.create(name="P", project_lead=person)
    ProjectCommitment.objects.create(
        practitioner=practitioner, project=project, hours_per_day=8
    )
    project.auditors.add(practitioner)
    campaign = Campaign.objects.create(project=project, name="C")
    mission = Mission.objects.create(campaign=campaign, title="M")
    return user, project, campaign, mission, practitioner


class AdjacentStatusTests(TestCase):
    """Pure function, so no database at all."""

    def test_walks_forward(self):
        self.assertEqual(adjacent_status(TaskStatus.TODO, "next"), TaskStatus.IN_PROGRESS)
        self.assertEqual(adjacent_status(TaskStatus.IN_PROGRESS, "next"), TaskStatus.DONE)

    def test_walks_back(self):
        self.assertEqual(adjacent_status(TaskStatus.DONE, "prev"), TaskStatus.IN_PROGRESS)
        self.assertEqual(adjacent_status(TaskStatus.IN_PROGRESS, "prev"), TaskStatus.TODO)

    def test_stops_at_both_ends(self):
        self.assertIsNone(adjacent_status(TaskStatus.DONE, "next"))
        self.assertIsNone(adjacent_status(TaskStatus.TODO, "prev"))

    def test_unknown_value_behaves_as_todo(self):
        self.assertEqual(adjacent_status("nonsense", "next"), TaskStatus.IN_PROGRESS)


class TaskCompletedAtInvariantTests(TestCase):
    def setUp(self):
        Platform.objects.create(
            site_name="Test", author="Test", publication_year=2026, active=True
        )
        self.user, self.project, _, self.mission, _ = _make_world()
        self.task = Task.objects.create(mission=self.mission, title="T", weight=3)
        self.client.force_login(self.user)

    # ── save() ───────────────────────────────────────────────────────────────

    def test_saving_done_stamps_completed_at(self):
        self.task.status = TaskStatus.DONE
        self.task.save()
        self.assertIsNotNone(self.task.completed_at)

    def test_saving_done_again_keeps_the_original_timestamp(self):
        self.task.status = TaskStatus.DONE
        self.task.save()
        first = self.task.completed_at
        self.task.title = "renamed"
        self.task.save()
        self.assertEqual(self.task.completed_at, first)

    def test_leaving_done_clears_completed_at(self):
        self.task.status = TaskStatus.DONE
        self.task.save()
        self.task.status = TaskStatus.IN_PROGRESS
        self.task.save()
        self.assertIsNone(self.task.completed_at)

    def test_update_fields_status_also_persists_completed_at(self):
        """The trap: without widening update_fields the value is never written."""
        self.task.status = TaskStatus.DONE
        self.task.save(update_fields=["status"])
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.DONE)
        self.assertIsNotNone(self.task.completed_at)

    def test_update_fields_leaving_done_persists_the_clear(self):
        self.task.status = TaskStatus.DONE
        self.task.save()
        self.task.status = TaskStatus.TODO
        self.task.save(update_fields=["status"])
        self.task.refresh_from_db()
        self.assertIsNone(self.task.completed_at)

    def test_completed_at_written_without_done_is_reverted(self):
        self.task.completed_at = timezone.now()
        self.task.save()
        self.task.refresh_from_db()
        self.assertIsNone(self.task.completed_at)

    def test_explicit_timestamp_on_a_done_task_survives(self):
        """Seeds and migrations backdate completions; save must not overwrite."""
        when = timezone.now() - timedelta(days=3)
        task = Task.objects.create(
            mission=self.mission, title="old", status=TaskStatus.DONE, completed_at=when
        )
        task.refresh_from_db()
        self.assertEqual(task.completed_at, when)

    # ── the database, for paths save() never sees ────────────────────────────

    def test_db_rejects_done_without_completed_at(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Task.objects.filter(pk=self.task.pk).update(
                    status=TaskStatus.DONE, completed_at=None
                )

    def test_db_rejects_completed_at_without_done(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Task.objects.filter(pk=self.task.pk).update(
                    status=TaskStatus.TODO, completed_at=timezone.now()
                )

    # ── HTML views ───────────────────────────────────────────────────────────

    def _promote(self):
        return self.client.post(
            f"/kanban/{self.project.pk}/task/{self.task.pk}/promote/"
        )

    def _demote(self):
        return self.client.post(
            f"/kanban/{self.project.pk}/task/{self.task.pk}/demote/"
        )

    def test_html_promote_into_done_stamps_completed_at(self):
        self._promote()  # todo -> in_progress
        self._promote()  # in_progress -> done
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.DONE)
        self.assertIsNotNone(self.task.completed_at)

    def test_html_demote_out_of_done_clears_completed_at(self):
        self._promote()
        self._promote()
        self._demote()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.IN_PROGRESS)
        self.assertIsNone(self.task.completed_at)

    def test_html_promote_rejects_get(self):
        """These mutate state; as GET endpoints any prefetch could move a card."""
        res = self.client.get(f"/kanban/{self.project.pk}/task/{self.task.pk}/promote/")
        self.assertEqual(res.status_code, 405)

    def test_html_promote_refused_for_a_non_auditor(self):
        other = User.objects.create_user(username="outsider", password="pass")
        self.client.force_login(other)
        self._promote()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.TODO)

    # ── JSON API ─────────────────────────────────────────────────────────────

    def test_api_promote_into_done_stamps_completed_at(self):
        self.client.post(f"/kanban/api/tasks/{self.task.pk}/promote/")
        res = self.client.post(f"/kanban/api/tasks/{self.task.pk}/promote/")
        self.assertEqual(res.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.DONE)
        self.assertIsNotNone(self.task.completed_at)

    def test_api_demote_out_of_done_clears_completed_at(self):
        self.client.post(f"/kanban/api/tasks/{self.task.pk}/promote/")
        self.client.post(f"/kanban/api/tasks/{self.task.pk}/promote/")
        self.client.post(f"/kanban/api/tasks/{self.task.pk}/demote/")
        self.task.refresh_from_db()
        self.assertIsNone(self.task.completed_at)

    def test_api_patch_to_done_stamps_completed_at(self):
        res = self.client.patch(
            f"/kanban/api/tasks/{self.task.pk}/",
            data='{"status": "done"}',
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.completed_at)

    def test_api_patch_rejects_an_unknown_status(self):
        res = self.client.patch(
            f"/kanban/api/tasks/{self.task.pk}/",
            data='{"status": "shipped"}',
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)

    def test_api_patch_rejects_column_id(self):
        """Refused loudly rather than silently ignored."""
        res = self.client.patch(
            f"/kanban/api/tasks/{self.task.pk}/",
            data='{"column_id": 3}',
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)

    def test_api_patch_rejects_a_non_integer_weight(self):
        res = self.client.patch(
            f"/kanban/api/tasks/{self.task.pk}/",
            data='{"weight": "abc"}',
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)

    def test_api_patch_rejects_a_weight_off_the_scale(self):
        res = self.client.patch(
            f"/kanban/api/tasks/{self.task.pk}/",
            data='{"weight": -5}',
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)

    def test_api_promote_refused_for_a_non_auditor(self):
        other = add_to_mesh(User.objects.create_user(username="apioutsider", password="pass"))
        self.client.force_login(other)
        res = self.client.post(f"/kanban/api/tasks/{self.task.pk}/promote/")
        self.assertEqual(res.status_code, 403)

    # ── the two halves agreeing ──────────────────────────────────────────────

    def test_every_write_path_leaves_status_and_timestamp_in_step(self):
        self.client.post(f"/kanban/api/tasks/{self.task.pk}/promote/")
        self.client.post(f"/kanban/{self.project.pk}/task/{self.task.pk}/promote/")
        self.client.patch(
            f"/kanban/api/tasks/{self.task.pk}/",
            data='{"status": "in_progress"}',
            content_type="application/json",
        )
        self.assertEqual(
            Task.objects.filter(status=TaskStatus.DONE).count(),
            Task.objects.filter(completed_at__isnull=False).count(),
        )

    def test_metrics_agree_with_the_board_after_an_api_promote(self):
        """The one test that would have caught the original divergence."""
        self.client.post(f"/kanban/api/tasks/{self.task.pk}/promote/")
        self.client.post(f"/kanban/api/tasks/{self.task.pk}/promote/")

        summary = SprintMetricsCalculator(self.project).get_summary()
        self.assertEqual(summary["completed_tasks"], 1)
        self.assertEqual(summary["open_tasks"], 0)


class TaskRelationTests(TestCase):
    def setUp(self):
        self.user, self.project, self.campaign, self.mission, self.practitioner = _make_world()
        self.a = Task.objects.create(mission=self.mission, title="implement ananas")
        self.b = Task.objects.create(mission=self.mission, title="test ananas")
        self.c = Task.objects.create(mission=self.mission, title="implement banana")

    def test_a_relation_reads_forwards_and_backwards(self):
        TaskRelation.objects.create(
            from_task=self.a, to_task=self.b, relation_type=RelationType.TESTS
        )
        forward = self.a.relations()[0]
        self.assertEqual(forward["other"], self.b)
        self.assertEqual(str(forward["label"]), "tests")

        reverse = self.b.relations()[0]
        self.assertEqual(reverse["other"], self.a)
        self.assertEqual(str(reverse["label"]), "is tested by")

    def test_relations_across_campaigns_are_refused(self):
        other_campaign = Campaign.objects.create(project=self.project, name="Other")
        other_mission = Mission.objects.create(campaign=other_campaign, title="OM")
        stranger = Task.objects.create(mission=other_mission, title="elsewhere")

        with self.assertRaises(ValidationError):
            TaskRelation.objects.create(
                from_task=self.a, to_task=stranger, relation_type=RelationType.BLOCKS
            )

    def test_a_task_cannot_relate_to_itself(self):
        with self.assertRaises(ValidationError):
            TaskRelation.objects.create(
                from_task=self.a, to_task=self.a, relation_type=RelationType.RELATES
            )

    def test_duplicate_edges_are_refused(self):
        TaskRelation.objects.create(
            from_task=self.a, to_task=self.b, relation_type=RelationType.BLOCKS
        )
        with self.assertRaises(ValidationError):
            TaskRelation.objects.create(
                from_task=self.a, to_task=self.b, relation_type=RelationType.BLOCKS
            )

    def test_symmetric_relations_are_stored_in_one_direction(self):
        """So the mirror collides with the unique constraint rather than duplicating."""
        high, low = sorted([self.a, self.b], key=lambda t: t.pk, reverse=True)
        relation = TaskRelation.objects.create(
            from_task=high, to_task=low, relation_type=RelationType.RELATES
        )
        relation.refresh_from_db()
        self.assertEqual(relation.from_task_id, low.pk)
        self.assertEqual(relation.to_task_id, high.pk)

    def test_the_mirror_of_a_symmetric_relation_is_refused(self):
        TaskRelation.objects.create(
            from_task=self.a, to_task=self.b, relation_type=RelationType.RELATES
        )
        with self.assertRaises(ValidationError):
            TaskRelation.objects.create(
                from_task=self.b, to_task=self.a, relation_type=RelationType.RELATES
            )

    def test_a_direct_blocking_cycle_is_refused(self):
        TaskRelation.objects.create(
            from_task=self.a, to_task=self.b, relation_type=RelationType.BLOCKS
        )
        with self.assertRaises(ValidationError):
            TaskRelation.objects.create(
                from_task=self.b, to_task=self.a, relation_type=RelationType.BLOCKS
            )

    def test_a_longer_blocking_cycle_is_refused(self):
        TaskRelation.objects.create(
            from_task=self.a, to_task=self.b, relation_type=RelationType.BLOCKS
        )
        TaskRelation.objects.create(
            from_task=self.b, to_task=self.c, relation_type=RelationType.BLOCKS
        )
        with self.assertRaises(ValidationError):
            TaskRelation.objects.create(
                from_task=self.c, to_task=self.a, relation_type=RelationType.BLOCKS
            )

    def test_a_cycle_mixing_blocks_and_precedes_is_refused(self):
        """The ordering family is checked as a whole, not per type."""
        TaskRelation.objects.create(
            from_task=self.a, to_task=self.b, relation_type=RelationType.BLOCKS
        )
        with self.assertRaises(ValidationError):
            TaskRelation.objects.create(
                from_task=self.b, to_task=self.a, relation_type=RelationType.PRECEDES
            )

    def test_a_non_ordering_cycle_is_fine(self):
        """"relates" and "tests" imply no order, so a loop of them means nothing bad."""
        TaskRelation.objects.create(
            from_task=self.a, to_task=self.b, relation_type=RelationType.TESTS
        )
        TaskRelation.objects.create(
            from_task=self.b, to_task=self.a, relation_type=RelationType.DUPLICATES
        )
        self.assertEqual(TaskRelation.objects.count(), 2)

    def test_open_blockers_lists_only_unfinished_blockers(self):
        TaskRelation.objects.create(
            from_task=self.a, to_task=self.c, relation_type=RelationType.BLOCKS
        )
        self.assertEqual(list(self.c.open_blockers()), [self.a])

        self.a.status = TaskStatus.DONE
        self.a.save()
        self.assertEqual(list(self.c.open_blockers()), [])

    def test_open_blockers_ignores_other_relation_types(self):
        TaskRelation.objects.create(
            from_task=self.a, to_task=self.c, relation_type=RelationType.RELATES
        )
        self.assertEqual(list(self.c.open_blockers()), [])

    def test_deleting_a_task_takes_its_relations(self):
        TaskRelation.objects.create(
            from_task=self.a, to_task=self.b, relation_type=RelationType.BLOCKS
        )
        self.a.delete()
        self.assertEqual(TaskRelation.objects.count(), 0)


class BlockedPromotionTests(TestCase):
    """Blockers advise; they do not refuse."""

    def setUp(self):
        Platform.objects.create(
            site_name="Test", author="Test", publication_year=2026, active=True
        )
        self.user, self.project, _, self.mission, _ = _make_world()
        self.blocker = Task.objects.create(mission=self.mission, title="implement ananas")
        self.blocked = Task.objects.create(mission=self.mission, title="implement banana")
        TaskRelation.objects.create(
            from_task=self.blocker, to_task=self.blocked, relation_type=RelationType.BLOCKS
        )
        self.client.force_login(self.user)

    def test_a_blocked_task_still_moves(self):
        self.client.post(f"/kanban/{self.project.pk}/task/{self.blocked.pk}/promote/")
        self.blocked.refresh_from_db()
        self.assertEqual(self.blocked.status, TaskStatus.IN_PROGRESS)

    def test_moving_a_blocked_task_warns(self):
        res = self.client.post(
            f"/kanban/{self.project.pk}/task/{self.blocked.pk}/promote/", follow=True
        )
        messages = [str(m) for m in res.context["messages"]]
        self.assertTrue(any("blocker" in message for message in messages), messages)

    def test_no_warning_once_the_blocker_is_done(self):
        self.blocker.status = TaskStatus.DONE
        self.blocker.save()
        res = self.client.post(
            f"/kanban/{self.project.pk}/task/{self.blocked.pk}/promote/", follow=True
        )
        messages = [str(m) for m in res.context["messages"]]
        self.assertFalse(any("blocker" in message for message in messages), messages)


class ReviewerGateTests(TestCase):
    def setUp(self):
        Platform.objects.create(
            site_name="Test", author="Test", publication_year=2026, active=True
        )
        self.user, self.project, _, self.mission, self.practitioner = _make_world()

        reviewer_user = User.objects.create_user(username="reviewer", password="pass")
        reviewer_person = Person.objects.create(
            user=reviewer_user, display_name="Rev", email="rev@x.com"
        )
        self.reviewer = Practitioner.objects.create(person=reviewer_person, role="reviewer")
        self.project.auditors.add(self.reviewer)

        self.task = Task.objects.create(
            mission=self.mission, title="T", reviewer=self.reviewer,
            status=TaskStatus.IN_PROGRESS,
        )
        self.reviewer_user = reviewer_user

    def test_someone_else_cannot_complete_a_reviewed_task(self):
        self.client.force_login(self.user)
        self.client.post(f"/kanban/{self.project.pk}/task/{self.task.pk}/promote/")
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.IN_PROGRESS)

    def test_the_reviewer_can(self):
        self.client.force_login(self.reviewer_user)
        self.client.post(f"/kanban/{self.project.pk}/task/{self.task.pk}/promote/")
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.DONE)

    def test_the_gate_only_applies_on_the_way_to_done(self):
        """It used to key on "the last column by position", which an added column moved."""
        self.task.status = TaskStatus.TODO
        self.task.save()
        self.client.force_login(self.user)
        self.client.post(f"/kanban/{self.project.pk}/task/{self.task.pk}/promote/")
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.IN_PROGRESS)
