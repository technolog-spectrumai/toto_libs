import base64
import hashlib

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from toto.notarius import contract_format as cf
from toto.notarius import latex
from toto.notarius.defaults import DEFAULT_CONTRACT_LATEX
from toto.notarius.models import ContractTemplate

User = get_user_model()


class ContractFormatTests(TestCase):
    def test_round_trip_preserves_structure(self):
        c = cf.new_contract("NDA", "Acme", "a@b.c", doc_type="nda")
        c.parties[0].representative = cf.Representative(id="rep-1", name="Rep", title="CEO")
        c.parties.append(cf.Party(id="party-2", role="signer", type="person", legal_name="Bob"))
        c2 = cf.loads(cf.dumps(c))
        self.assertEqual((c2.title, c2.doc_type), ("NDA", "nda"))
        self.assertEqual(len(c2.parties), 2)
        self.assertEqual(c2.parties[0].representative.name, "Rep")

    def test_content_hash_matches_sha256(self):
        c = cf.new_contract("x")
        c.content = cf.Content(encoding="text", data="hello")
        self.assertEqual(c.computed_content_hash(), hashlib.sha256(b"hello").hexdigest())

    def test_add_signature_flips_status_to_signed(self):
        c = cf.new_contract("x", "Acme")  # single issuer party
        self.assertEqual(c.status, "draft")
        cf.add_signature(c, cf.Signature(id="sig-1", party="party-1", status="completed"))
        self.assertEqual(c.status, "signed")

    def test_dict_round_trip_for_editor(self):
        c = cf.new_contract("Doc", "Acme")
        c3 = cf.Contract.from_dict(c.to_dict())
        self.assertEqual(c3.parties[0].legal_name, "Acme")


class ContractTemplateTests(TestCase):
    def test_for_type_prefers_match_then_default(self):
        default = ContractTemplate.objects.create(name="Default", key="default", latex_source="x", is_default=True)
        nda = ContractTemplate.objects.create(name="NDA", key="nda", latex_source="y")
        self.assertEqual(ContractTemplate.for_type("nda"), nda)
        self.assertEqual(ContractTemplate.for_type("unknown"), default)
        self.assertEqual(ContractTemplate.for_type(""), default)


class LatexRenderTests(TestCase):
    def test_default_template_renders(self):
        c = cf.new_contract("Title & Co", "Acme & Sons")
        c.content = cf.Content(media_type="application/pdf", encoding="base64",
                               data=base64.b64encode(b"%PDF-1.4").decode())
        tex = latex.render_latex(
            c, DEFAULT_CONTRACT_LATEX,
            signature_images=[{"party_name": "Acme", "typed_name": "", "signed_at": "",
                               "method": "ed25519", "filename": "sig-1.png"}],
            content_pdf_filename="content.pdf",
        )
        self.assertIn("Title", tex)
        self.assertIn(r"\&", tex)               # & escaped by |latexescape
        self.assertIn(r"\includepdf", tex)      # embedded original PDF
        self.assertIn("content.pdf", tex)
        self.assertIn("sig-1.png", tex)         # handwritten appearance referenced

    def test_latexescape_filter(self):
        from toto.notarius.templatetags.notarius_latex import latexescape
        self.assertEqual(latexescape("a & b_c %"), r"a \& b\_c \%")


class ContractSigningTests(TestCase):
    """Electronic signature over the file-based canonical payload, verified from the
    embedded public key alone (no DB row needed)."""

    def test_sign_and_verify_roundtrip(self):
        from toto.gervazy.crypto import GervazyCryptoSession
        from toto.gervazy.signing import SigningService
        from toto.people.models import Person

        user = User.objects.create_user("signer", password="x")
        person = Person.objects.create(user=user, display_name="Signer")
        session, wrapped_key = GervazyCryptoSession.initialize_strongbox(user, "box", "pw")

        c = cf.new_contract("Deal", "Acme")
        content_hash = c.computed_content_hash()
        signed_at = timezone.now().isoformat(timespec="seconds")
        payload = SigningService.canonical_contract_file_payload(
            c.id, c.version, content_hash, "party-1", signed_at)
        doc_sig = SigningService.sign_document(session, person, payload, wrapped_key=wrapped_key)

        # Verify with the public key carried in the signature — self-contained.
        self.assertTrue(SigningService.verify_with_public_key(
            doc_sig.public_key_pem, payload, doc_sig.signature_b64))

        # Any change to the canonical payload must fail verification.
        tampered = SigningService.canonical_contract_file_payload(
            c.id, c.version, content_hash, "party-2", signed_at)
        self.assertFalse(SigningService.verify_with_public_key(
            doc_sig.public_key_pem, tampered, doc_sig.signature_b64))
