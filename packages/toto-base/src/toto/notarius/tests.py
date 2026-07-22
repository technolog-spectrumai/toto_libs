import base64
import hashlib
import json
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.notarius import contract_format as cf
from toto.notarius import latex
from toto.notarius.defaults import DEFAULT_CONTRACT_LATEX
from toto.notarius.models import ContractTemplate
from toto.vault.models import Bucket, VaultDirectory, VaultFile

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

    def test_is_contract_detects_signing_document(self):
        self.assertTrue(cf.is_contract(cf.dumps(cf.new_contract("x"))))
        self.assertTrue(cf.is_contract(b"<signingDocument></signingDocument>"))
        self.assertFalse(cf.is_contract("<notes><a/></notes>"))
        self.assertFalse(cf.is_contract("not xml at all <"))
        self.assertFalse(cf.is_contract(""))


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


class NotariusViewTests(TestCase):
    """Contracts as ordinary .xml vault files: create / list / open / sign flows."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._ov = override_settings(MEDIA_ROOT=self._tmp)
        self._ov.enable()
        self.addCleanup(self._ov.disable)
        Platform.objects.create(site_name="Toto", author="T", publication_year=2026, active=True)
        self.alice = User.objects.create_user("alice", password="pw", email="alice@example.com")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)
        self.directory = VaultDirectory.objects.create(name="Docs", bucket=self.bucket, owner=self.alice)

    # ── helpers ─────────────────────────────────────────────────────

    def _person(self, user, email):
        from toto.people.models import Person
        return Person.objects.create(user=user, display_name=user.username, email=email)

    def _strongbox(self, user):
        from toto.gervazy.crypto import GervazyCryptoSession
        from toto.gervazy.models import UserStrongbox
        GervazyCryptoSession.initialize_strongbox(user, "box", "pw")
        return UserStrongbox.objects.filter(owner=user).first()

    def _make_contract(self, owner=None, is_public=False, title="Deal", key="c1", extra_parties=None):
        owner = owner or self.alice
        c = cf.new_contract(title=title, issuer_name="Acme", issuer_email="acme@example.com")
        for pid, role, name, email in (extra_parties or []):
            c.parties.append(cf.Party(id=pid, role=role, type="person", legal_name=name, email=email))
        xml = cf.dumps(c).encode("utf-8")
        return VaultFile.objects.create(
            owner=owner, title=f"{title}.xml", key=key, file_type="xml",
            is_public=is_public, bucket=self.bucket, directory=self.directory,
            file=SimpleUploadedFile(f"{key}.xml", xml, content_type="application/xml"),
        )

    def _load(self, vf):
        vf.refresh_from_db()
        with vf.file.storage.open(vf.file.name, "rb") as fh:
            return cf.loads(fh.read().decode("utf-8"))

    # ── create ──────────────────────────────────────────────────────

    def test_create_makes_ordinary_xml_and_redirects_to_edit(self):
        self.client.force_login(self.alice)
        res = self.client.post(reverse("notarius:create"), data={
            "filename": "My NDA", "title": "NDA",
            "bucket_id": str(self.bucket.pk), "directory_id": str(self.directory.pk),
        })
        self.assertEqual(res.status_code, 302)
        vf = VaultFile.objects.filter(owner=self.alice, file_type="xml").latest("pk")
        self.assertEqual(res.url, reverse("notarius:edit", args=[vf.pk]))
        self.assertEqual(vf.file_type, "xml")
        self.assertTrue(vf.title.endswith(".xml"))
        self.assertEqual(vf.bucket, self.bucket)
        self.assertEqual(vf.directory, self.directory)
        c = self._load(vf)
        self.assertEqual(c.title, "NDA")
        self.assertTrue(cf.is_contract(cf.dumps(c)))

    def test_create_requires_login(self):
        res = self.client.post(reverse("notarius:create"), data={"filename": "x"})
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.url)

    # ── list ────────────────────────────────────────────────────────

    def test_index_lists_contracts_only_with_path(self):
        self._make_contract(title="RealDeal", key="real")
        VaultFile.objects.create(  # a plain (non-contract) xml must be excluded
            owner=self.alice, title="notes.xml", key="notes", file_type="xml",
            bucket=self.bucket, directory=self.directory,
            file=SimpleUploadedFile("notes.xml", b"<notes><a/></notes>"),
        )
        self.client.force_login(self.alice)
        res = self.client.get(reverse("notarius:index"))
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertIn("RealDeal", body)
        self.assertNotIn("notes.xml", body)
        self.assertIn("Lab / Docs", body)

    # ── open gate ───────────────────────────────────────────────────

    def test_view_404_on_non_contract_xml(self):
        vf = VaultFile.objects.create(
            owner=self.alice, title="notes.xml", key="notes2", file_type="xml",
            bucket=self.bucket, file=SimpleUploadedFile("notes.xml", b"<notes/>"),
        )
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("notarius:view", args=[vf.pk])).status_code, 404)

    def test_view_denied_for_private_other_user(self):
        # Bucket-root private contract owned by Alice — Bob has no access path.
        # (A file in a no-whitelist *directory* would be open to all authed users,
        # which is the vault's intended "open folder" semantics.)
        bob = User.objects.create_user("bob", password="pw")
        xml = cf.dumps(cf.new_contract(title="Secret", issuer_name="Acme")).encode("utf-8")
        vf = VaultFile.objects.create(
            owner=self.alice, title="Secret.xml", key="priv", file_type="xml",
            is_public=False, bucket=self.bucket,  # no directory → bucket root
            file=SimpleUploadedFile("priv.xml", xml),
        )
        self.client.force_login(bob)
        self.assertEqual(self.client.get(reverse("notarius:view", args=[vf.pk])).status_code, 404)

    # ── edit save ───────────────────────────────────────────────────

    def test_save_preserves_signatures_and_edits_metadata(self):
        vf = self._make_contract(key="save")
        from toto.notarius.views import _write
        c = self._load(vf)
        cf.add_signature(c, cf.Signature(id="sig-1", party="party-1", status="completed", method="ed25519"))
        _write(vf, c)

        self.client.force_login(self.alice)
        payload = {
            "id": c.id, "title": "Renamed", "status": "draft",
            "parties": [{"id": "party-1", "role": "issuer", "type": "organization",
                         "legal_name": "Acme", "email": "acme@example.com"}],
            "content": {"id": "content-1", "encoding": "text", "media_type": "text/plain", "data": "hi"},
        }
        res = self.client.post(reverse("notarius:save", args=[vf.pk]),
                               data=json.dumps(payload), content_type="application/json")
        self.assertEqual(res.status_code, 200)
        c2 = self._load(vf)
        self.assertEqual(c2.title, "Renamed")
        self.assertEqual(len(c2.signatures), 1)  # signature preserved from disk

    # ── signing authorization ───────────────────────────────────────

    def test_sign_matching_party_records_verified_signature(self):
        carol = User.objects.create_user("carol", password="pw", email="carol@example.com")
        self._person(carol, "carol@example.com")
        sb = self._strongbox(carol)
        vf = self._make_contract(owner=carol, key="sc",
                                 extra_parties=[("party-2", "signer", "Carol", "carol@example.com")])
        self.client.force_login(carol)
        res = self.client.post(reverse("notarius:sign", args=[vf.pk]), data={
            "party_id": "party-2", "strongbox_password": "pw",
            "strongbox_id": str(sb.pk), "typed_name": "Carol", "signature_data": "",
        })
        self.assertEqual(res.status_code, 302)  # success → redirect to view
        c = self._load(vf)
        self.assertEqual(len(c.signatures), 1)
        s = c.signatures[0]
        from toto.gervazy.signing import SigningService
        payload = SigningService.canonical_contract_file_payload(
            c.id, c.version, s.signed_hash, s.party, s.signed_at)
        self.assertTrue(SigningService.verify_with_public_key(
            s.public_key_pem, payload, s.signature_value))

    def test_sign_blocked_for_non_matching_party(self):
        carol = User.objects.create_user("carol2", password="pw", email="carol2@example.com")
        self._person(carol, "carol2@example.com")
        self._strongbox(carol)
        vf = self._make_contract(owner=self.alice, is_public=True, key="blk",
                                 extra_parties=[("party-2", "signer", "Other", "other@example.com")])
        self.client.force_login(carol)
        res = self.client.post(reverse("notarius:sign", args=[vf.pk]), data={
            "party_id": "party-2", "strongbox_password": "pw",
            "typed_name": "x", "signature_data": "",
        })
        self.assertEqual(res.status_code, 200)  # re-rendered with an error, not signed
        self.assertEqual(len(self._load(vf).signatures), 0)

    def test_owner_can_sign_any_party(self):
        self._person(self.alice, "alice@example.com")
        sb = self._strongbox(self.alice)
        vf = self._make_contract(owner=self.alice, key="own",
                                 extra_parties=[("party-2", "signer", "Other", "other@example.com")])
        self.client.force_login(self.alice)
        res = self.client.post(reverse("notarius:sign", args=[vf.pk]), data={
            "party_id": "party-2", "strongbox_password": "pw",
            "strongbox_id": str(sb.pk), "typed_name": "Alice", "signature_data": "",
        })
        self.assertEqual(res.status_code, 302)
        self.assertEqual(len(self._load(vf).signatures), 1)

    # ── vault type retired ──────────────────────────────────────────

    def test_contract_type_retired_from_vault_new_file(self):
        from toto.vault.views import CREATABLE_TYPES, CreateEmptyFileView
        self.assertNotIn("contract", {t for t, _ in CREATABLE_TYPES})
        self.assertNotIn("contract", CreateEmptyFileView._ALLOWED)
        self.assertNotIn("contract", CreateEmptyFileView._INITIAL)
