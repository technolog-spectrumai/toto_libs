"""A mission's attachments list only the files the viewer may still read
(2026-10-01). The foreign key finds a trashed file — the vault's base
manager sees the trash — so the list showed its title with a dead link, and
it never asked the vault whether a file attached once is still readable.
The view now hands the template ``toto.vault.attach.visible``.

    manage.py test toto.kanban.tests_trashed_attachments \\
        --settings=toto.kanban.testing.settings
"""

import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.kanban.models import (
    Campaign, Mission, MissionAttachment, Practitioner, Project, ProjectCommitment,
)
from toto.people.models import Person

User = get_user_model()


def _person(username):
    user = User.objects.create_user(username=username, password="pass")
    person = Person.objects.create(user=user, display_name=username.title(),
                                   email=f"{username}@x.com")
    return user, person


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="kanban-trashed-attach-"))
class TrashedAttachmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.vault.models import Bucket

        Platform.objects.create(site_name="Test", author="t", publication_year=2026,
                                active=True)
        cls.lead_user, cls.lead = _person("lead")
        cls.member_user, cls.member = _person("member")
        for user in (cls.lead_user, cls.member_user):
            add_to_mesh(user)
        cls.project = Project.objects.create(name="P", project_lead=cls.lead)
        seat = Practitioner.objects.create(person=cls.member, role="contributor")
        ProjectCommitment.objects.create(practitioner=seat, project=cls.project,
                                         hours_per_day=4)
        campaign = Campaign.objects.create(project=cls.project, name="C")
        cls.mission = Mission.objects.create(campaign=campaign, title="M")
        cls.bucket = Bucket.objects.create(name="Team", slug="team", owner=cls.member_user)

    def setUp(self):
        self.spec = self._attach(self.member_user, "spec.pdf", "spec")
        self.plan = self._attach(self.member_user, "old-plan.pdf", "old-plan")
        self.secret = self._attach(self.lead_user, "secret.pdf", "secret", bucket=None)

    def _attach(self, owner, title, key, *, bucket=False):
        from toto.vault.models import VaultFile

        vault_file = VaultFile.objects.create(
            owner=owner, title=title, key=key, file_type="pdf",
            bucket=self.bucket if bucket is False else bucket,
            file=SimpleUploadedFile(title, b"%PDF-1.4 bytes"))
        MissionAttachment.objects.create(mission=self.mission, vault_file=vault_file)
        return vault_file

    def _page(self, user):
        self.client.force_login(user)
        response = self.client.get(f"/kanban/mission/{self.mission.pk}/")
        self.assertEqual(response.status_code, 200)
        return response

    def _listed(self, response):
        return [a.vault_file.title for a in response.context["attachments"]]

    def test_a_trashed_file_is_not_listed_and_leaves_no_dead_link(self):
        link = self.plan.get_public_url()
        self.assertTrue(link)
        self.plan.trash(self.member_user)
        response = self._page(self.member_user)
        self.assertEqual(self._listed(response), ["spec.pdf"])
        body = response.content.decode()
        self.assertIn("spec.pdf", body)
        self.assertNotIn("old-plan.pdf", body)
        self.assertNotIn(link, body)

    def test_a_file_the_viewer_may_not_read_is_not_listed(self):
        self.assertEqual(self._listed(self._page(self.member_user)),
                         ["spec.pdf", "old-plan.pdf"])
        self.assertNotIn("secret.pdf", self._page(self.member_user).content.decode())
        # Its owner still sees it — and not the member's private files.
        self.assertEqual(self._listed(self._page(self.lead_user)), ["secret.pdf"])

    def test_a_restored_file_is_listed_again(self):
        from toto.vault.models import VaultFile
        from toto.vault.trash import restore_file

        self.plan.trash(self.member_user)
        restore_file(VaultFile.all_objects.get(pk=self.plan.pk), by=self.member_user)
        self.assertEqual(self._listed(self._page(self.member_user)),
                         ["spec.pdf", "old-plan.pdf"])

    def test_the_count_is_of_what_is_listed(self):
        self.plan.trash(self.member_user)
        response = self._page(self.member_user)
        self.assertEqual(len(response.context["attachments"]), 1)
