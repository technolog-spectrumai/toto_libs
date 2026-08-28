"""An export has to be checkable by somebody who cannot ask us anything."""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

from django.test import TestCase

from toto.ledger.canonical import canonicalize, digest
from toto.ledger.models import LedgerKind
from toto.ledger.services import chain, export


class ExportTestCase(TestCase):
    def setUp(self):
        self.ledger = chain.open_ledger(
            key="acme-actions", name="Acme actions", kind=LedgerKind.COMPANY,
            scope_type="company.company",
        )
        for n in range(1, 4):
            chain.append(ledger=self.ledger, payload={"n": n, "body": f"action {n}"},
                         source_type="company.action", source_ref=f"Action {n}")


class ChainDocumentTests(ExportTestCase):
    def test_it_is_well_formed_xml(self):
        root = ElementTree.fromstring(export.chain_document(self.ledger))
        self.assertEqual(root.tag, "ledger")

    def test_it_names_the_algorithm_and_format(self):
        root = ElementTree.fromstring(export.chain_document(self.ledger))
        self.assertEqual(root.get("algorithm"), "sha256")
        self.assertEqual(root.get("format"), "bc-ledger-xml-1")

    def test_it_carries_every_block_in_order(self):
        root = ElementTree.fromstring(export.chain_document(self.ledger))
        sequences = [
            int(block.find("sequence").text)
            for block in root.find("blocks").findall("block")
        ]
        self.assertEqual(sequences, [1, 2, 3, 4])

    def test_a_third_party_can_verify_it_with_nothing_but_the_file(self):
        """The point of the export: no database, no ORM, no this codebase.

        This is the auditor's whole procedure — parse the file with a stdlib
        XML library, canonicalize each block by the documented rule, hash it,
        and check the links. Note the canonicalize step: `ElementTree` writes
        an empty element as `<x />` where this codebase writes `<x></x>`, so
        hashing whatever bytes a parser happens to emit would make an honest
        verifier report a forgery. Canonicalizing is what removes the parser
        from the answer, and it is ~20 lines anyone can reimplement.
        """
        document = export.chain_document(self.ledger)
        root = ElementTree.fromstring(document)
        algorithm = root.get("algorithm")

        previous = ""
        for block in root.find("blocks").findall("block"):
            block_text = canonicalize(ElementTree.tostring(block, encoding="unicode"))
            self.assertEqual(block.find("previous-hash").text or "", previous)
            previous = digest(block_text, algorithm=algorithm)

        # The last hash reached must be the head block's stored hash.
        self.assertEqual(previous, self.ledger.head.entry_hash)

    def test_exporting_twice_gives_identical_bytes(self):
        self.assertEqual(
            export.chain_document(self.ledger), export.chain_document(self.ledger),
        )


class ZipManifestTests(ExportTestCase):
    def archive(self):
        return zipfile.ZipFile(io.BytesIO(export.chain_zip(self.ledger)))

    def test_it_holds_a_manifest_a_document_and_one_file_per_block(self):
        names = set(self.archive().namelist())
        self.assertIn("manifest.xml", names)
        self.assertIn("ledger.xml", names)
        for sequence in range(1, 5):
            self.assertIn(f"blocks/{sequence:06d}.xml", names)

    def test_the_manifest_names_the_algorithm_and_every_block_hash(self):
        manifest = ElementTree.fromstring(
            self.archive().read("manifest.xml").decode("utf-8")
        )
        self.assertEqual(manifest.get("algorithm"), "sha256")
        self.assertEqual(manifest.get("blocks"), "4")
        hashes = [block.get("hash") for block in manifest.findall("block")]
        stored = list(
            self.ledger.entries.order_by("sequence").values_list("entry_hash", flat=True)
        )
        self.assertEqual(hashes, stored)

    def test_each_block_file_hashes_to_what_the_manifest_claims(self):
        """Block files are stored canonical, so hashing them needs no extra step."""
        archive = self.archive()
        manifest = ElementTree.fromstring(archive.read("manifest.xml").decode("utf-8"))
        algorithm = manifest.get("algorithm")
        for block in manifest.findall("block"):
            with self.subTest(sequence=block.get("sequence")):
                text = archive.read(block.get("file")).decode("utf-8")
                self.assertEqual(digest(text, algorithm=algorithm), block.get("hash"))

    def test_a_single_block_file_is_the_same_block_the_document_carries(self):
        """Same block, whichever way it is taken out of the archive."""
        archive = self.archive()
        document = ElementTree.fromstring(archive.read("ledger.xml").decode("utf-8"))
        first_in_document = canonicalize(ElementTree.tostring(
            document.find("blocks").findall("block")[0], encoding="unicode",
        ))
        self.assertEqual(archive.read("blocks/000001.xml").decode("utf-8"),
                         first_in_document)

    def test_the_archive_is_byte_for_byte_reproducible(self):
        """Fixed timestamps: two exports of one chain must diff as identical."""
        self.assertEqual(export.chain_zip(self.ledger), export.chain_zip(self.ledger))

    def test_the_filename_says_how_long_the_chain_was(self):
        self.assertEqual(
            export.export_filename(self.ledger, "zip"), "ledger-acme-actions-000004.zip",
        )


class TamperedExportTests(ExportTestCase):
    def test_an_edited_export_stops_verifying(self):
        document = export.chain_document(self.ledger)
        edited = document.replace("action 2", "action two")
        self.assertNotEqual(document, edited)

        root = ElementTree.fromstring(edited)
        algorithm = root.get("algorithm")
        blocks = root.find("blocks").findall("block")
        recomputed = [
            digest(canonicalize(ElementTree.tostring(block, encoding="unicode")),
                   algorithm=algorithm)
            for block in blocks
        ]
        stored = list(
            self.ledger.entries.order_by("sequence").values_list("entry_hash", flat=True)
        )
        self.assertNotEqual(recomputed, stored)
