"""The Business Center's exports: built here, rendered by aralia."""

from __future__ import annotations

import base64
import os
import tempfile
from unittest import mock, skipUnless
from xml.etree import ElementTree

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.aralia import dispatch, render as render_mod
from toto.aralia.models import AraliaRun, AraliaUsageEvent, RunStatus
from toto.core.models import Platform
from toto.documents import builders, services
from toto.ledger import checkpoint
from toto.ledger.services import chain
from toto.vault.models import VaultFile

MEDIA = tempfile.mkdtemp(prefix="bc-documents-")
PDF = skipUnless(render_mod.is_available(), "WeasyPrint is not installed in this build")

#: The host's crest, the letterhead's last logo fallback: aralia refuses a
#: company document with no logo at all, and these tests set none.
CREST = os.path.join(MEDIA, "crest.png")
with open(CREST, "wb") as _handle:
    _handle.write(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
        "IQAAAABJRU5ErkJggg=="))


@override_settings(MEDIA_ROOT=MEDIA, PLATFORM_LOGO_PATH=CREST)
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
    """Aralia renders an approved source and nothing else, so an export is
    filed as a vault page and aralia resolves that page (2026-10-01)."""

    def export(self, html="<p>x</p>", **kwargs):
        with mock.patch.object(dispatch, "dispatch_run", side_effect=lambda run: run):
            return services.export(html, user=self.user, **kwargs)

    def test_an_empty_document_is_refused(self):
        with self.assertRaises(services.ExportRefused):
            services.export("   ", user=self.user)

    def test_an_export_is_a_vault_page_aralia_resolves(self):
        run = self.export(builders.ledger_document(self.ledger), label="ledger acme")
        page = run.source_file
        self.assertEqual((run.source_kind, run.source_ref), ("file", str(page.pk)))
        self.assertEqual((page.title, page.file_type, page.owner),
                         ("ledger-acme.html", "html", self.user))
        self.assertFalse(page.is_public)
        self.assertEqual(page.bucket.slug, "personal-ada")
        self.assertEqual(page.directory.name, services.EXPORT_FOLDER)
        # The PDF lands beside its page, named after it.
        self.assertEqual(run.target_directory, page.directory)
        self.assertEqual(run.output_name, "ledger-acme.pdf")

    def test_the_run_is_a_company_document(self):
        """The federation's letterhead, the body, the footer naming the run."""
        run = self.export(builders.ledger_document(self.ledger), label="ledger acme")
        self.assertEqual(run.template_slug, "federal")
        self.assertIn('class="logo"', run.html)
        self.assertIn("<main>", run.html)
        self.assertIn("Acme actions", run.html)
        self.assertIn("aralia-provenance", run.html)
        self.assertIn(f"render {run.pk}", run.html)

    def test_the_page_goes_through_the_sanitiser(self):
        run = self.export("<html><head><title>Old title</title></head><body>"
                          "<h1 onclick='x()'>Minutes</h1><script>alert(1)</script>"
                          "</body></html>")
        self.assertIn("<h1>Minutes</h1>", run.html)
        self.assertNotIn("alert(1)", run.html)
        self.assertNotIn("onclick", run.html)
        # The page's head is not printed: the letterhead is the head.
        self.assertNotIn("Old title", run.html)

    def test_two_exports_of_one_name_are_two_pages(self):
        first = self.export(label="register Acme")
        second = self.export(label="register Acme")
        self.assertNotEqual(first.source_file_id, second.source_file_id)
        self.assertEqual([first.output_name, second.output_name],
                         ["register-acme.pdf", "register-acme-2.pdf"])

    def test_a_queued_export_is_not_charged_before_its_pdf(self):
        """Aralia bills a render that produced a PDF, after it did."""
        run = self.export()
        self.assertIsInstance(run, AraliaRun)
        self.assertFalse(AraliaUsageEvent.objects.exists())

    @override_settings(BUILD_WEASYPRINT=True)
    def test_no_worker_refuses_and_charges_nothing(self):
        with mock.patch.object(dispatch, "worker_available", return_value=False):
            with self.assertRaises(services.ExportRefused) as caught:
                services.export("<p>x</p>", user=self.user)
        self.assertIn("No PDF worker", str(caught.exception))
        self.assertEqual(AraliaRun.objects.get().status, RunStatus.FAILED)
        self.assertFalse(AraliaUsageEvent.objects.exists())

    @override_settings(PLATFORM_LOGO_PATH="")
    def test_no_logo_refuses_and_files_nothing(self):
        with self.assertRaises(services.ExportRefused) as caught:
            services.export("<p>x</p>", user=self.user)
        self.assertIn("No logo", str(caught.exception))
        self.assertFalse(VaultFile.objects.exists())
        self.assertFalse(AraliaRun.objects.exists())

    def test_in_arrears_refuses_and_files_nothing(self):
        """Quota and funds are aralia's check, made before anything exists."""
        from toto.quota.api import InArrears

        with mock.patch("toto.aralia.billing.check_before_dispatch",
                        side_effect=InArrears()):
            with self.assertRaises(services.ExportRefused) as caught:
                services.export("<p>x</p>", user=self.user)
        self.assertIn("unpaid", str(caught.exception))
        self.assertFalse(VaultFile.objects.exists())
        self.assertFalse(AraliaRun.objects.exists())

    def test_a_document_too_large_for_aralia_is_refused_before_filing(self):
        from toto.aralia.sources import MAX_SOURCE_BYTES

        with self.assertRaises(services.ExportRefused):
            services.export("x" * (MAX_SOURCE_BYTES + 1), user=self.user)
        self.assertFalse(VaultFile.objects.exists())

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
        # The QR survives the sanitiser: a data: picture is all it lets through.
        self.assertIn('<img src="data:image/png;base64,', run.html)
        self.assertIn('alt="Verification code"', run.html)

    @override_settings(BUILD_WEASYPRINT=True)
    def test_a_refused_export_says_why_and_stays_on_the_page(self):
        with mock.patch.object(dispatch, "worker_available", return_value=False):
            response = self.client.post(
                reverse("ledger:export_pdf", args=[self.ledger.uid]), follow=True,
            )
        self.assertContains(response, "No PDF worker")

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
        with mock.patch.object(dispatch, "dispatch_run", side_effect=lambda run: run):
            run = services.export(html, user=self.user, label="ledger acme")
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
        # The QR is a real embedded image, not just markup that mentioned one:
        # the letterhead's logo is the first picture, the QR the second.
        self.assertEqual(len(reader.pages[0].images), 2)
        # Filed beside its page, in the requester's export folder.
        self.assertEqual(finished.output.directory, run.source_file.directory)

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
