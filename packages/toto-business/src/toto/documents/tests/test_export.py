"""The Business Center's exports: built here, rendered by aralia."""

from __future__ import annotations

import tempfile
from unittest import mock, skipUnless
from xml.etree import ElementTree

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.aralia import dispatch, render as render_mod
from toto.aralia.models import AraliaRun, AraliaUsageEvent
from toto.core.models import Platform
from toto.documents import builders, services
from toto.ledger import checkpoint
from toto.ledger.services import chain

MEDIA = tempfile.mkdtemp(prefix="bc-documents-")
PDF = skipUnless(render_mod.is_available(), "WeasyPrint is not installed in this build")


@override_settings(MEDIA_ROOT=MEDIA)
class DocumentTestCase(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        self.user = get_user_model().objects.create_user("ada", password="x")
        self.ledger = chain.open_ledger(key="acme", name="Acme actions")
        for n in (1, 2):
            chain.append(ledger=self.ledger, payload={"n": n},
                         source_type="company.action", source_ref=f"Action {n}")


class BuilderTests(DocumentTestCase):
    def test_it_builds_one_self_contained_html_document(self):
        html = builders.ledger_document(self.ledger)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("<style>", html)
        self.assertIn("@page", html)

    def test_it_carries_no_page_furniture(self):
        """A print document must not inherit the site's navigation."""
        html = builders.ledger_document(self.ledger)
        for leaked in ("x-data", "darkMode", "oya/base", "<nav"):
            self.assertNotIn(leaked, html)

    def test_it_lists_every_block_in_order(self):
        html = builders.ledger_document(self.ledger)
        self.assertIn("Action 1", html)
        self.assertIn("Action 2", html)
        self.assertLess(html.index("Action 1"), html.index("Action 2"))

    def test_it_states_the_verdict(self):
        html = builders.ledger_document(self.ledger)
        self.assertIn("Verified", html)
        self.assertIn("3 blocks", html)          # genesis + two

    def test_a_broken_chain_says_so_in_the_document(self):
        from toto.ledger.tests.test_verify import tamper

        entry = self.ledger.entries.order_by("sequence")[1]
        tamper(entry.pk, source_ref="rewritten")
        html = builders.ledger_document(self.ledger)
        self.assertIn("does not verify", html)

    def test_it_escapes_content_rather_than_letting_it_break_the_markup(self):
        chain.append(ledger=self.ledger, payload={"n": 3},
                     source_type="x", source_ref="<script>alert(1)</script>")
        html = builders.ledger_document(self.ledger)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_the_qr_is_embedded_as_a_data_uri(self):
        """aralia refuses to fetch anything, so it has to be inline."""
        payload = checkpoint.take(ledger=self.ledger).payload
        html = builders.ledger_document(self.ledger, checkpoint_payload=payload)
        self.assertIn("data:image/png;base64,", html)
        # The payload is HTML-escaped in the document, as it must be — `&` in
        # the query string would otherwise be markup.
        from html import escape

        self.assertIn(escape(payload), html)

    def test_the_payload_text_is_printed_beside_the_picture(self):
        """A code that will not scan still has to be usable."""
        payload = checkpoint.take(ledger=self.ledger).payload
        html = builders.ledger_document(self.ledger, checkpoint_payload=payload)
        self.assertIn("class='payload'", html)

    def test_a_document_without_a_checkpoint_simply_has_no_code(self):
        html = builders.ledger_document(self.ledger)
        self.assertNotIn("data:image/png", html)


class ExportServiceTests(DocumentTestCase):
    def test_an_empty_document_is_refused(self):
        with self.assertRaises(services.ExportRefused):
            services.export("   ", user=self.user)

    def test_a_queued_export_is_metered_once(self):
        with mock.patch.object(dispatch, "dispatch_run", side_effect=lambda run: run):
            run = services.export("<p>x</p>", user=self.user, label="ledger acme")
        self.assertIsInstance(run, AraliaRun)
        self.assertEqual(AraliaUsageEvent.objects.count(), 1)

    def test_no_worker_refuses_and_refunds(self):
        with mock.patch.object(dispatch, "worker_available", return_value=False), \
             mock.patch("toto.documents.services.refund_for") as refund:
            with self.assertRaises(services.ExportRefused):
                services.export("<p>x</p>", user=self.user)
        refund.assert_called_once()

    def test_the_business_center_owns_no_renderer(self):
        """It must reach WeasyPrint only through aralia."""
        import ast
        from pathlib import Path

        root = Path(services.__file__).resolve().parent
        for path in sorted(root.rglob("*.py")):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    self.assertNotIn("weasyprint", name.lower(),
                                     f"{path.name} imports a renderer directly")


class ExportViewTests(DocumentTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_the_ledger_page_offers_the_export(self):
        response = self.client.get(reverse("ledger:detail", args=[self.ledger.uid]))
        self.assertContains(response, reverse("ledger:export_pdf", args=[self.ledger.uid]))
        self.assertContains(response, "Export PDF")

    def test_exporting_takes_a_fresh_checkpoint(self):
        """A printed ledger must attest to the state in the reader's hand."""
        from toto.ledger.models import LedgerCheckpoint

        with mock.patch.object(dispatch, "dispatch_run", side_effect=lambda run: run):
            self.client.post(reverse("ledger:export_pdf", args=[self.ledger.uid]))
        self.assertEqual(LedgerCheckpoint.objects.count(), 1)
        self.assertEqual(LedgerCheckpoint.objects.get().entry_count, 3)

    def test_exporting_queues_a_render(self):
        with mock.patch.object(dispatch, "dispatch_run", side_effect=lambda run: run):
            self.client.post(reverse("ledger:export_pdf", args=[self.ledger.uid]))
        run = AraliaRun.objects.get()
        self.assertIn("Acme actions", run.html)
        self.assertIn("data:image/png;base64,", run.html)

    def test_a_refused_export_says_why_and_stays_on_the_page(self):
        with mock.patch.object(dispatch, "worker_available", return_value=False):
            response = self.client.post(
                reverse("ledger:export_pdf", args=[self.ledger.uid]), follow=True,
            )
        self.assertContains(response, "No worker")

    def test_it_requires_login(self):
        self.client.logout()
        response = self.client.post(reverse("ledger:export_pdf", args=[self.ledger.uid]))
        self.assertEqual(response.status_code, 302)


@PDF
class EndToEndTests(DocumentTestCase):
    """Build it, render it, and read the PDF back."""

    def test_the_ledger_renders_to_a_pdf_that_says_what_it_should(self):
        import io

        import pypdf

        from toto.aralia.runner import execute_run

        payload = checkpoint.take(ledger=self.ledger).payload
        html = builders.ledger_document(self.ledger, checkpoint_payload=payload)
        run = dispatch.create_run(html=html, user=self.user)
        finished = execute_run(run.pk)

        self.assertEqual(finished.status, "success")
        finished.output.file.open("rb")
        try:
            pdf = finished.output.file.read()
        finally:
            finished.output.file.close()

        reader = pypdf.PdfReader(io.BytesIO(pdf))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("Acme actions", text)
        self.assertIn("Action 1", text)
        self.assertIn("Verified", text)
        # The QR is a real embedded image, not just markup that mentioned one.
        self.assertTrue(any(reader.pages[0].images))

    def test_the_printed_code_verifies_against_the_live_chain(self):
        """The whole promise, end to end."""
        payload = checkpoint.take(ledger=self.ledger).payload
        self.assertEqual(checkpoint.verify_text(payload).verdict, "MATCH")

    def test_the_printed_code_reports_a_later_tamper(self):
        from toto.ledger.tests.test_verify import tamper

        payload = checkpoint.take(ledger=self.ledger).payload
        entry = self.ledger.entries.order_by("sequence")[1]
        tamper(entry.pk, payload_xml="<payload><map></map></payload>")
        self.assertEqual(checkpoint.verify_text(payload).verdict, "MISMATCH")
