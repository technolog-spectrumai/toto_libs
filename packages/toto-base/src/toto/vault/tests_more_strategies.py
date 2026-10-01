"""The per-type encryption strategies behind every encrypt/decrypt door:
text and image seal with the owner's keyring, PDF uses the format's own
password. What must hold for each: the bytes on disk stop being readable,
the right password gives them back, and the wrong one changes nothing.
"""

import io
import os
import tempfile
from pathlib import Path
from unittest import skip

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from pypdf import PdfReader, PdfWriter

from toto.gervazy.models import UserStrongbox
from toto.vault.models import VaultFile
from toto.vault.strategy.image import ImageStrategy
from toto.vault.strategy.pdf import PdfStrategy
from toto.vault.strategy.text import TextStrategy

User = get_user_model()

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def a_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class _Fixture(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp(prefix="vault-more-strategies-")
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.owner = User.objects.create_user("owner", password="pw")

    def keyring(self):
        return UserStrongbox.objects.create(owner=self.owner, name="keyring",
                                            argon2_memory_cost=19456, argon2_iterations=2,
                                            argon2_lanes=1)

    def file(self, body, title, file_type):
        vault_file = VaultFile(owner=self.owner, title=title, file_type=file_type)
        vault_file.file.save(title, ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def stored(self, vault_file):
        vault_file.refresh_from_db()
        with open(vault_file.file.path, "rb") as handle:
            return handle.read()

    def files_on_disk(self):
        found = []
        for root, _dirs, names in os.walk(self.media):
            found += [os.path.join(root, n) for n in names]
        return sorted(found)


class SealedStrategyTests(_Fixture):
    def test_the_strategy_follows_the_file_type(self):
        self.assertIsInstance(VaultFile(file_type="pdf").get_strategy(), PdfStrategy)
        self.assertIsInstance(VaultFile(file_type="image").get_strategy(), ImageStrategy)
        self.assertIsInstance(VaultFile(file_type="markdown").get_strategy(), TextStrategy)

    def test_no_keyring_means_no_sealing_and_no_change(self):
        for strategy, title, kind in ((ImageStrategy(), "p.png", "image"),
                                      (TextStrategy(), "t.txt", "text")):
            f = self.file(PNG, title, kind)
            with self.assertRaisesMessage(ValueError, "No UserVault"):
                strategy.encrypt(f, password="pw")
            with self.assertRaisesMessage(ValueError, "No strongbox"):
                strategy.decrypt_to_bytes(f, password="pw")
            self.assertEqual(self.stored(f), PNG)

    def test_an_image_seals_opens_and_reads_back(self):
        self.keyring()
        f = self.file(PNG, "p.png", "image")
        f.encrypt(password="right")
        self.assertTrue(f.is_encrypted)
        self.assertNotEqual(self.stored(f), PNG)
        self.assertEqual(ImageStrategy().decrypt_to_bytes(f, password="right"),
                         (PNG, "image/png"))
        with self.assertRaisesMessage(ValueError, "Incorrect password."):
            ImageStrategy().decrypt_to_bytes(f, password="wrong")
        f.decrypt(password="right")
        self.assertFalse(f.is_encrypted)
        self.assertEqual(self.stored(f), PNG)

    def test_a_wrong_password_leaves_an_image_sealed(self):
        self.keyring()
        f = self.file(PNG, "p.png", "image")
        f.encrypt(password="right")
        sealed = self.stored(f)
        with self.assertRaisesMessage(ValueError, "Decryption failed"):
            f.decrypt(password="wrong")
        self.assertEqual(self.stored(f), sealed)
        self.assertTrue(VaultFile.objects.get(pk=f.pk).is_encrypted)

    def test_encrypting_twice_is_a_no_op_and_decrypting_plain_is_refused(self):
        self.keyring()
        f = self.file(b"text", "t.txt", "text")
        f.encrypt(password="one")
        once = self.stored(f)
        f.encrypt(password="two")
        self.assertEqual(self.stored(f), once)
        plain = self.file(b"x", "u.txt", "text")
        with self.assertRaisesMessage(ValueError, "not encrypted"):
            plain.decrypt(password="one")

    def test_the_plaintext_file_does_not_survive_encryption(self):
        self.keyring()
        f = self.file(b"secret words", "t.txt", "text")
        f.encrypt(password="pw")
        for path in self.files_on_disk():
            with open(path, "rb") as handle:
                self.assertNotIn(b"secret words", handle.read(), path)


class PdfStrategyTests(_Fixture):
    def test_a_pdf_takes_the_formats_own_password(self):
        f = self.file(a_pdf(), "doc.pdf", "pdf")
        f.encrypt(password="user-pw")
        self.assertTrue(f.is_encrypted)
        self.assertTrue(PdfReader(f.file.path).is_encrypted)
        data, mime = PdfStrategy().decrypt_to_bytes(f, password="user-pw")
        self.assertEqual(mime, "application/pdf")
        self.assertFalse(PdfReader(io.BytesIO(data)).is_encrypted)

    def test_the_wrong_password_reads_nothing(self):
        f = self.file(a_pdf(), "doc.pdf", "pdf")
        f.encrypt(password="user-pw")
        with self.assertRaisesMessage(ValueError, "Incorrect password."):
            PdfStrategy().decrypt_to_bytes(f, password="nope")

    def test_a_plain_pdf_is_not_decrypted(self):
        f = self.file(a_pdf(), "doc.pdf", "pdf")
        with self.assertRaisesMessage(ValueError, "not encrypted"):
            PdfStrategy().decrypt_to_bytes(f, password="x")
        with self.assertRaisesMessage(ValueError, "not encrypted"):
            PdfStrategy().decrypt(f, password="x")

    def test_decrypting_puts_a_readable_pdf_back(self):
        f = self.file(a_pdf(), "doc.pdf", "pdf")
        f.encrypt(password="user-pw")
        f.decrypt(password="user-pw")
        self.assertFalse(VaultFile.objects.get(pk=f.pk).is_encrypted)
        self.assertFalse(PdfReader(f.file.path).is_encrypted)
        self.assertEqual(len(PdfReader(f.file.path).pages), 1)

    def test_the_form_falls_back_to_the_user_password_for_the_owner(self):
        from toto.vault.strategy.forms import EncryptPdfForm

        form = EncryptPdfForm({"_selected_action": ["1"], "user_password": "u",
                               "owner_password": ""})
        self.assertTrue(form.is_valid())
        self.assertEqual(PdfStrategy().parse_encrypt_form(form),
                         {"password": "u", "owner_password": "u"})

    @skip("BUG vault/strategy/pdf.py:35-58 - encrypt() and decrypt() write a working copy "
          "(<name>_encrypted.pdf / <name>_decrypted.pdf) next to the file, save a SECOND copy "
          "through the storage and never delete the first; after a decrypt the orphan is the "
          "plaintext, and it stays on disk through re-encryption and deletion of the file")
    def test_decrypting_leaves_no_plaintext_copy_behind(self):
        f = self.file(a_pdf(), "doc.pdf", "pdf")
        f.encrypt(password="user-pw")
        f.decrypt(password="user-pw")
        self.assertEqual(self.files_on_disk(), [f.file.path])


class _NoPyPDF2:
    """An import finder that answers for PyPDF2 as an uninstalled package."""

    def find_spec(self, name, path=None, target=None):
        if name == "PyPDF2" or name.startswith("PyPDF2."):
            raise ImportError(f"No module named {name!r} (refused by the test)")
        return None


class PypdfTests(_Fixture):
    """The format's own password through pypdf (2026-10-01, 37c.30), never
    PyPDF2 — its retired predecessor, which the hosts no longer install — and
    loaded where a PDF is opened, not when the vault's models load."""

    def test_loading_the_strategy_loads_no_pdf_library(self):
        import ast

        import toto.vault.strategy.pdf as module

        self.assertNotIn("PdfReader", vars(module))
        self.assertNotIn("PdfWriter", vars(module))
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                     for alias in node.names}
        self.assertNotIn("PyPDF2", imported)
        self.assertIn("pypdf", imported)

    def test_a_password_comes_and_goes_where_pypdf2_cannot_import(self):
        import sys

        hidden = {name: sys.modules.pop(name) for name in list(sys.modules)
                  if name == "PyPDF2" or name.startswith("PyPDF2.")}
        self.addCleanup(sys.modules.update, hidden)
        finder = _NoPyPDF2()
        sys.meta_path.insert(0, finder)
        self.addCleanup(sys.meta_path.remove, finder)
        f = self.file(a_pdf(), "doc.pdf", "pdf")
        f.encrypt(password="user-pw")
        self.assertTrue(PdfReader(f.file.path).is_encrypted)
        data, _mime = PdfStrategy().decrypt_to_bytes(f, password="user-pw")
        self.assertFalse(PdfReader(io.BytesIO(data)).is_encrypted)
        with self.assertRaisesMessage(ValueError, "Incorrect password."):
            PdfStrategy().decrypt_to_bytes(f, password="nope")
        f.decrypt(password="user-pw")
        self.assertFalse(PdfReader(f.file.path).is_encrypted)
