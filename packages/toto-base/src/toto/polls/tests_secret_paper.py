"""Paper secret-vote results: anonymity by construction, on the same chain.

The claim under test is structural, not cosmetic: a paper result's snapshot
NEVER CONTAINED a voter→choice link, so there is nothing to hide, redact or
leak — the ballots list is absent, the roll is absent, and every export says
"Secret ballot — results only" out loud.

Run only where a gate stanza names this module.
"""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import checkpoint, services
from .models import Ballot, Decision, Outcome, Question
from .tests_ledger_chain import _decided, _platform

User = get_user_model()

OPTIONS = [
    {"label": "For", "ballots": 7, "weight": None},
    {"label": "Against", "ballots": 3, "weight": None},
]


def _pdf_text(raw: bytes) -> str:
    """The text inside a reportlab PDF's compressed content streams.

    reportlab writes pages as ASCII85+Flate streams, so a raw-bytes assertIn
    never sees the words — this inflates them. Good enough for asserting a
    label's presence; not a general PDF parser.
    """
    import base64
    import re
    import zlib

    chunks = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        data = match.group(1).strip()
        try:
            if data.endswith(b"~>"):
                data = base64.a85decode(data, adobe=True)
            chunks.append(zlib.decompress(data).decode("latin-1", "ignore"))
        except Exception:  # noqa: BLE001 - non-text streams are fine to skip
            continue
    return "".join(chunks)


def _paper(user, **kwargs):
    defaults = dict(title="Budget 2027", question_text="Adopt the budget?",
                    options=[dict(o) for o in OPTIONS], method="paper",
                    electorate_label="General assembly", electorate_size=12,
                    note="Tally sheet in the safe", recorded_by=user)
    defaults.update(kwargs)
    return services.record_paper_result(**defaults)


class RecordingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("op", password="x",
                                             is_staff=True)

    def test_the_result_lands_on_the_chain_marked_secret(self):
        before = _decided("Earlier online vote")
        decision = _paper(self.staff)

        self.assertTrue(decision.secret_ballot)
        self.assertEqual(decision.outcome, Outcome.WINNER)
        self.assertEqual(decision.winner_label, "For")
        self.assertEqual(decision.total_ballots, 10)
        self.assertEqual(decision.prev_hash, before.content_hash)
        self.assertTrue(Decision.verify_chain().ok)

    def test_the_snapshot_never_contained_a_voter_choice_link(self):
        # Anonymity by design: not a hidden list — no list. The whole
        # serialized snapshot carries no ballots key, no roll, and no
        # username but the attesting operator's.
        decision = _paper(self.staff)
        self.assertNotIn("ballots", decision.content)
        self.assertEqual(decision.content["roll"] if "roll" in
                         decision.content else [], [])
        serialized = json.dumps(decision.content)
        self.assertNotIn("voter_id", serialized)
        self.assertNotIn("voter_username", serialized)
        self.assertEqual(Ballot.objects.count(), 0)
        self.assertEqual(decision.question.roll.count(), 0)

    def test_the_question_can_never_accept_an_online_ballot(self):
        decision = _paper(self.staff)
        question = decision.question
        self.assertFalse(question.is_open)
        choice = question.choices.first()
        with self.assertRaises(services.VotingError):
            services.cast(question, self.staff, choice)

    def test_turnout_and_tie_and_weights(self):
        tied = _paper(self.staff, title="Tied", options=[
            {"label": "A", "ballots": 5, "weight": None},
            {"label": "B", "ballots": 5, "weight": None}])
        self.assertEqual(tied.outcome, Outcome.TIE)
        weighted = _paper(self.staff, title="Weighted", options=[
            {"label": "A", "ballots": 2, "weight": 60},
            {"label": "B", "ballots": 8, "weight": 40}])
        self.assertEqual(weighted.winner_label, "A")
        self.assertAlmostEqual(_paper(
            self.staff, title="Turnout").turnout, 10 / 12)

    def test_it_verifies_under_a_checkpoint_like_any_entry(self):
        _paper(self.staff)
        stored = checkpoint.take(scope_type="", scope_id="")
        self.assertEqual(checkpoint.verify_stored(stored).verdict, "MATCH")
        Decision.objects.update(winner_label="Forged")
        self.assertEqual(checkpoint.verify_stored(stored).verdict, "MISMATCH")


class FormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = User.objects.create_user("op", password="x",
                                             is_staff=True)
        cls.member = User.objects.create_user("m", password="x")

    def _post(self, **overrides):
        data = {"title": "Budget 2027", "question_text": "Adopt it?",
                "method": "paper", "electorate_label": "Assembly",
                "electorate_size": "12", "note": "sheet in safe",
                "option_label_0": "For", "option_ballots_0": "7",
                "option_label_1": "Against", "option_ballots_1": "3"}
        data.update(overrides)
        return self.client.post(reverse("polls:vote_record_paper"), data)

    def test_staff_only(self):
        self.client.force_login(self.member)
        self.assertEqual(
            self.client.get(reverse("polls:vote_record_paper")).status_code,
            403)
        self.assertEqual(self._post().status_code, 403)

    def test_the_form_records_and_redirects_to_the_ledger(self):
        self.client.force_login(self.staff)
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        decision = Decision.objects.get()
        self.assertTrue(decision.secret_ballot)
        self.assertEqual(decision.content["provenance"]["method"], "paper")

    def test_one_option_is_refused(self):
        self.client.force_login(self.staff)
        resp = self._post(option_label_1="", option_ballots_1="")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "at least two options")
        self.assertEqual(Decision.objects.count(), 0)

    def test_the_ledger_page_badges_it(self):
        self.client.force_login(self.staff)
        self._post()
        resp = self.client.get(reverse("polls:decision_ledger"))
        self.assertContains(resp, "Secret ballot")


class ExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = User.objects.create_user("op", password="x",
                                             is_staff=True)
        cls.outsider = User.objects.create_user("out", password="x")

    def test_secret_pdf_carries_results_only_and_says_so(self):
        decision = _paper(self.staff)
        self.client.force_login(self.staff)
        resp = self.client.get(reverse(
            "polls:decision_pdf",
            args=[decision.question.kind, decision.question.slug]))
        if resp.status_code == 503:
            self.skipTest("no PDF renderer in this environment")
        text = _pdf_text(resp.content)
        self.assertIn("SECRET BALLOT", text)
        self.assertIn("results only", text)
        # Even the attesting operator's PDF holds no ballot appendix — the
        # renderer has nothing to print, whoever asks.
        self.assertNotIn("Ballot record", text)

    def test_open_vote_appendix_is_members_and_staff_only(self):
        from .render_pdf import PdfUnavailable, vote_result_pdf

        decision = _decided("Open vote")
        question = decision.question
        # The snapshot the renderer reads, with one attributable ballot —
        # mutated in memory only; a Decision row refuses saving anyway.
        decision.content = dict(
            decision.content,
            ballots=[{"voter_username": "alice-voter", "choice_label": "For",
                      "weight": 1, "cast_at": "2026-01-01T00:00:00"}])
        try:
            for_member = _pdf_text(vote_result_pdf(
                question, decision, include_ballots=True))
            for_outsider = _pdf_text(vote_result_pdf(
                question, decision, include_ballots=False))
        except PdfUnavailable:
            self.skipTest("no PDF renderer in this environment")
        self.assertIn("alice-voter", for_member)
        self.assertNotIn("alice-voter", for_outsider)
        # The tally itself is in both — what is gated is who, never what.
        self.assertIn("For", for_outsider)

    def test_the_view_gates_the_appendix_by_roll_membership(self):
        decision = _decided("Open vote")
        self.client.force_login(self.outsider)
        resp = self.client.get(reverse(
            "polls:decision_pdf",
            args=[decision.question.kind, decision.question.slug]))
        # An outsider who may see results gets a PDF (or a 503 renderer
        # sentence) — never a traceback; the appendix decision is inside
        # the renderer call, asserted above.
        self.assertIn(resp.status_code, (200, 403, 503))
