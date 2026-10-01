"""The voting pages, and the whole vote-to-chain flow through them."""

from __future__ import annotations

import base64
import os
import tempfile
from decimal import Decimal
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.aralia import dispatch, render as render_mod
from toto.aralia.models import AraliaRun
from toto.company.integration import ledger as bc_ledger
from toto.company.integration import voting as bc_voting
from toto.company.models import CompanyMembership
from toto.company.tests.factories import CompanyFactoryMixin
from toto.core.models import Platform
from toto.people.models import Person
from toto.voting.models import (
    AttendanceStatus,
    Ballot,
    Meeting,
    ProposalStatus,
    Proposition,
    RollSource,
    VotingConfiguration,
)

MEDIA = tempfile.mkdtemp(prefix="bc-votes-")
PDF = skipUnless(render_mod.is_available(), "WeasyPrint is not installed in this build")

#: The host's crest, the letterhead's last logo fallback: aralia refuses a
#: company document with no logo at all, and these tests upload none.
CREST = os.path.join(MEDIA, "crest.png")
with open(CREST, "wb") as _handle:
    _handle.write(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
        "IQAAAABJRU5ErkJggg=="))


@override_settings(MEDIA_ROOT=MEDIA)
class VoteViewTestCase(CompanyFactoryMixin, TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        self.company = self.make_company()
        self.configuration = VotingConfiguration.objects.create(
            scope_type=bc_voting.SCOPE_TYPE, scope_uid=self.company.uid,
            name="Ordinary", slug="ordinary",
        )
        self.user = get_user_model().objects.create_user("ada", password="x")
        self.person = Person.objects.create(user=self.user, display_name="Ada")
        CompanyMembership.objects.create(company=self.company, person=self.person)

        self.other_user = get_user_model().objects.create_user("bob", password="x")
        self.other = Person.objects.create(user=self.other_user, display_name="Bob")
        CompanyMembership.objects.create(company=self.company, person=self.other)

        self.meeting = Meeting.objects.create(
            scope_type=bc_voting.SCOPE_TYPE, scope_uid=self.company.uid,
            title="Annual general meeting", configuration=self.configuration,
        )
        self.proposition = Proposition.objects.create(
            meeting=self.meeting, title="Adopt the 2027 plan",
            resolution_text="That the plan be adopted.",
            attachment_ref="vault:plan-2027.pdf",
            attachment_hash="a" * 64, attachment_label="The 2027 plan",
        )
        bc_voting.set_electorate(self.meeting, source=RollSource.MEMBERS,
                                 recorded_by=self.user)
        for entry in self.meeting.roll.all():
            entry.status = AttendanceStatus.PRESENT
            entry.save(update_fields=["status"], _allow_update=True)

    def detail_url(self):
        return reverse("company:vote_detail",
                       args=[self.company.slug, self.meeting.uid])

    def open_voting(self):
        self.client.force_login(self.user)
        self.client.post(reverse("company:vote_open",
                                 args=[self.company.slug, self.meeting.uid]))
        self.proposition.refresh_from_db()

    def cast(self, user, choice, confirm="yes", **meta):
        self.client.force_login(user)
        return self.client.post(
            reverse("company:vote_cast", args=[self.company.slug, self.meeting.uid,
                                               self.proposition.uid]),
            {"choice": choice, "confirm": confirm}, follow=True, **meta,
        )


class AccessTests(VoteViewTestCase):
    def test_the_pages_require_login(self):
        for name, args in (
            ("company:votes", [self.company.slug]),
            ("company:vote_detail", [self.company.slug, self.meeting.uid]),
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 302)
                self.assertIn("login", response["Location"])

    def test_a_non_member_cannot_open_voting(self):
        outsider = get_user_model().objects.create_user("zoe", password="x")
        self.client.force_login(outsider)
        response = self.client.post(
            reverse("company:vote_open", args=[self.company.slug, self.meeting.uid]),
            follow=True,
        )
        self.assertContains(response, "Only an active member")
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.status, "draft")


class DetailPageTests(VoteViewTestCase):
    def test_it_shows_participation_and_the_electorate(self):
        self.client.force_login(self.user)
        response = self.client.get(self.detail_url())
        self.assertContains(response, "Participation")
        self.assertContains(response, "Ada")
        self.assertContains(response, "Bob")

    def test_it_shows_the_proposition_and_its_attachment_hash(self):
        self.client.force_login(self.user)
        response = self.client.get(self.detail_url())
        self.assertContains(response, "Adopt the 2027 plan")
        self.assertContains(response, "The 2027 plan")
        self.assertContains(response, "aaaaaaaaaaaaaaaa")

    def test_the_ballot_table_names_every_voter_and_their_vote(self):
        """Voting is never secret, and the page is where that shows."""
        self.open_voting()
        self.cast(self.user, "for")
        self.cast(self.other_user, "against")
        self.client.force_login(self.user)
        response = self.client.get(self.detail_url())
        body = response.content.decode()

        # The ballot table exists, and both voters appear inside it with the
        # way they voted — not merely somewhere on the page, where the roll
        # would have put their names anyway.
        self.assertIn("Every ballot cast on this proposition", body)
        table = body[body.index("Every ballot cast on this proposition"):]
        for name, choice in (("Ada", "For"), ("Bob", "Against")):
            with self.subTest(voter=name):
                self.assertIn(name, table)
                self.assertIn(choice, table)

    def test_a_superseded_ballot_stays_visible(self):
        self.open_voting()
        self.cast(self.user, "for")
        self.cast(self.user, "against")
        self.client.force_login(self.user)
        response = self.client.get(self.detail_url())
        self.assertContains(response, "superseded")


class CastingTests(VoteViewTestCase):
    def test_casting_without_the_confirmation_is_refused(self):
        self.open_voting()
        response = self.cast(self.user, "for", confirm="")
        self.assertContains(response, "Tick the confirmation")
        self.assertFalse(Ballot.objects.exists())

    def test_a_confirmed_cast_is_recorded_with_its_evidence(self):
        self.open_voting()
        self.cast(self.user, "for")
        ballot = Ballot.objects.get()
        self.assertEqual(ballot.choice, "for")
        self.assertEqual(ballot.cast_by, self.user)
        self.assertIsNotNone(ballot.confirmed_at)
        self.assertEqual(ballot.auth_evidence["method"], "session+confirmation")
        self.assertEqual(ballot.auth_evidence["user"], "ada")

    @override_settings(TRUSTED_PROXIES=["172.16.0.0/12"])
    def test_the_evidence_names_the_voter_s_address_not_the_proxy_s(self):
        """nginx's X-Real-IP from a trusted proxy; a forged X-Forwarded-For
        is not read (2026-09-30)."""
        self.open_voting()
        self.cast(self.user, "for", REMOTE_ADDR="172.18.0.5", HTTP_X_REAL_IP="203.0.113.7",
                  HTTP_X_FORWARDED_FOR="198.51.100.66, 203.0.113.7")
        self.assertEqual(Ballot.objects.get().auth_evidence["ip"], "203.0.113.7")

    def test_casting_again_supersedes_and_only_the_last_counts(self):
        self.open_voting()
        self.cast(self.user, "for")
        self.cast(self.user, "against")
        self.assertEqual(Ballot.objects.count(), 2)
        self.assertEqual(Ballot.objects.filter(active=True).count(), 1)
        self.assertEqual(Ballot.objects.get(active=True).choice, "against")

    def test_somebody_not_on_the_roll_is_told_so(self):
        outsider = get_user_model().objects.create_user("zoe", password="x")
        Person.objects.create(user=outsider, display_name="Zoe")
        self.open_voting()
        self.client.force_login(outsider)
        response = self.client.post(
            reverse("company:vote_cast", args=[self.company.slug, self.meeting.uid,
                                               self.proposition.uid]),
            {"choice": "for", "confirm": "yes"}, follow=True,
        )
        self.assertContains(response, "not on this meeting")


class FinalizeFlowTests(VoteViewTestCase):
    def test_the_whole_flow_open_cast_finalize_chain(self):
        self.open_voting()
        self.cast(self.user, "for")
        self.cast(self.other_user, "for")

        self.client.force_login(self.user)
        self.client.post(reverse("company:vote_finalize",
                                 args=[self.company.slug, self.meeting.uid,
                                       self.proposition.uid]), follow=True)

        self.proposition.refresh_from_db()
        self.assertEqual(self.proposition.status, ProposalStatus.CLOSED)
        self.assertIsNotNone(self.proposition.block_uid)

        # It is on the chain, the chain verifies, and the block names the voters.
        ledger = bc_ledger.existing_ledger(self.company)
        entry = ledger.entries.get(source_type="voting.proposition")
        self.assertIn("Adopt the 2027 plan", entry.payload_xml)
        self.assertIn("Ada", entry.payload_xml)
        self.assertTrue(bc_ledger.verify_company_ledger(self.company).ok)

        # And it appears on the chain's own pages.
        chain_page = self.client.get(reverse("ledger:detail", args=[ledger.uid]))
        self.assertContains(chain_page, "Adopt the 2027 plan")
        block_page = self.client.get(
            reverse("ledger:block", args=[ledger.uid, entry.sequence]))
        self.assertContains(block_page, "Ada")

        # …and in the XML export, and the graph.
        export = self.client.get(reverse("ledger:export_xml", args=[ledger.uid]))
        self.assertIn(b"Adopt the 2027 plan", export.content)
        graph = self.client.get(reverse("ledger:graph", args=[ledger.uid])).json()
        self.assertEqual(len(graph["nodes"]), ledger.entries.count())

    def test_the_page_shows_the_frozen_result_after_finalization(self):
        self.open_voting()
        self.cast(self.user, "for")
        self.client.force_login(self.user)
        self.client.post(reverse("company:vote_finalize",
                                 args=[self.company.slug, self.meeting.uid,
                                       self.proposition.uid]))
        response = self.client.get(self.detail_url())
        self.assertContains(response, "adopted")
        self.assertContains(response, "On the chain as block")


@override_settings(PLATFORM_LOGO_PATH=CREST)
class ExportTests(VoteViewTestCase):
    """An export is filed as a vault page and rendered from it by aralia
    (toto.documents.services); the run's html is that page on the letterhead."""

    def test_the_meeting_record_queues_a_render(self):
        self.open_voting()
        self.cast(self.user, "for")
        self.client.force_login(self.user)
        with mock.patch.object(dispatch, "dispatch_run", side_effect=lambda run: run):
            self.client.post(reverse("company:meeting_export",
                                     args=[self.company.slug, self.meeting.uid]))
        run = AraliaRun.objects.get()
        self.assertIn("Annual general meeting", run.html)
        self.assertIn("Ada", run.html)

    def test_the_decision_export_queues_a_render(self):
        self.open_voting()
        self.cast(self.user, "for")
        self.client.force_login(self.user)
        with mock.patch.object(dispatch, "dispatch_run", side_effect=lambda run: run):
            self.client.post(reverse("company:vote_export",
                                     args=[self.company.slug, self.meeting.uid,
                                           self.proposition.uid]))
        run = AraliaRun.objects.get()
        self.assertIn("Adopt the 2027 plan", run.html)
        self.assertIn("a" * 16, run.html)          # the attachment hash

    def test_a_superseded_ballot_is_printed_too(self):
        self.open_voting()
        self.cast(self.user, "for")
        self.cast(self.user, "against")
        self.client.force_login(self.user)
        with mock.patch.object(dispatch, "dispatch_run", side_effect=lambda run: run):
            self.client.post(reverse("company:vote_export",
                                     args=[self.company.slug, self.meeting.uid,
                                           self.proposition.uid]))
        run = AraliaRun.objects.get()
        self.assertIn("superseded", run.html)

    @PDF
    def test_the_decision_really_renders_to_a_pdf(self):
        import io

        import pypdf

        from toto.aralia.runner import execute_run
        from toto.documents import builders, services

        self.open_voting()
        self.cast(self.user, "for")
        self.client.force_login(self.user)
        self.client.post(reverse("company:vote_finalize",
                                 args=[self.company.slug, self.meeting.uid,
                                       self.proposition.uid]))
        self.proposition.refresh_from_db()

        with mock.patch.object(dispatch, "dispatch_run", side_effect=lambda run: run):
            run = services.export(builders.vote_document(self.proposition),
                                  user=self.user, label="decision")
        finished = execute_run(run.pk)
        self.assertEqual(finished.status, "success")

        finished.output.file.open("rb")
        try:
            pdf = finished.output.file.read()
        finally:
            finished.output.file.close()
        text = "\n".join(page.extract_text() or "" for page in
                         pypdf.PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn("Adopt the 2027 plan", text)
        self.assertIn("adopted", text)
        self.assertIn("Ada", text)
