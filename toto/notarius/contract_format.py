"""
``.contract`` signing-document format — parse/serialize the ``<signingDocument>`` XML.

The ``.contract`` vault file is the single source of truth (like ``.pml``/``.tpy``):
parties, content, signatures and the audit trail all live inside the file. Signing
appends a ``<signature>`` element and flips ``<status>`` — this module never touches
the database. :func:`loads`/:func:`dumps` round-trip the XML; :meth:`Contract.to_dict`
/:meth:`Contract.from_dict` back the browser editor form.
"""
from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from xml.etree import ElementTree as ET


class ContractParseError(Exception):
    """Raised when the XML is not a parseable ``<signingDocument>``."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Representative:
    id: str = ""
    name: str = ""
    title: str = ""
    email: str = ""


@dataclass
class Party:
    id: str = ""
    type: str = "person"            # organization | person
    role: str = "signer"            # issuer | signer | witness
    legal_name: str = ""
    email: str = ""
    representative: Representative | None = None


@dataclass
class Content:
    id: str = "content-1"
    media_type: str = "text/plain"
    encoding: str = "base64"        # base64 | text
    data: str = ""                  # the (base64 or text) payload, whitespace-stripped


@dataclass
class Appearance:
    typed_name: str = ""
    image_b64: str = ""             # base64 PNG of the handwritten signature


@dataclass
class Signature:
    id: str = ""
    party: str = ""
    representative: str = ""
    target: str = "content-1"
    type: str = "electronic"
    status: str = "completed"
    signed_at: str = ""
    intent: str = "approve-and-sign"
    appearance: Appearance | None = None
    method: str = ""                # ed25519 | email-otp | digital-certificate
    signed_hash: str = ""
    signature_value: str = ""
    signature_algorithm: str = ""
    public_key_pem: str = ""        # our Ed25519 public key (in place of an X.509 cert)


@dataclass
class AuditEvent:
    type: str = ""
    actor: str = ""
    signature: str = ""
    timestamp: str = ""


@dataclass
class Contract:
    id: str = ""
    version: str = "1.0"
    title: str = ""
    doc_type: str = ""              # contract TYPE key — selects the admin LaTeX template
    created_at: str = ""
    status: str = "draft"           # draft | sent | partially-signed | signed
    parties: list[Party] = field(default_factory=list)
    content: Content = field(default_factory=Content)
    content_hash: str = ""
    content_hash_algorithm: str = "SHA-256"
    signatures: list[Signature] = field(default_factory=list)
    audit: list[AuditEvent] = field(default_factory=list)

    # -- helpers --------------------------------------------------------
    def party_by_id(self, pid: str) -> Party | None:
        return next((p for p in self.parties if p.id == pid), None)

    def computed_content_hash(self) -> str:
        """SHA-256 (hex) of the content payload — base64-decoded when encoded that way."""
        raw = (self.content.data or "").strip()
        if self.content.encoding == "base64":
            try:
                blob = base64.b64decode(raw, validate=False)
            except Exception:
                blob = raw.encode("utf-8")
        else:
            blob = raw.encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        # drop None representative/appearance for a cleaner editor payload
        for p in d["parties"]:
            if p["representative"] is None:
                p.pop("representative")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Contract":
        parties = []
        for p in d.get("parties", []):
            rep = p.get("representative")
            parties.append(Party(
                id=p.get("id", ""), type=p.get("type", "person"),
                role=p.get("role", "signer"), legal_name=p.get("legal_name", ""),
                email=p.get("email", ""),
                representative=Representative(**rep) if rep else None,
            ))
        c = d.get("content", {}) or {}
        sigs = []
        for s in d.get("signatures", []):
            ap = s.get("appearance")
            sigs.append(Signature(
                **{k: s.get(k, "") for k in (
                    "id", "party", "representative", "target", "type", "status",
                    "signed_at", "intent", "method", "signed_hash",
                    "signature_value", "signature_algorithm", "public_key_pem")},
                appearance=Appearance(**ap) if ap else None,
            ))
        return cls(
            id=d.get("id", ""), version=d.get("version", "1.0"),
            title=d.get("title", ""), doc_type=d.get("doc_type", ""),
            created_at=d.get("created_at", ""),
            status=d.get("status", "draft"), parties=parties,
            content=Content(
                id=c.get("id", "content-1"), media_type=c.get("media_type", "text/plain"),
                encoding=c.get("encoding", "base64"), data=c.get("data", ""),
            ),
            content_hash=d.get("content_hash", ""),
            content_hash_algorithm=d.get("content_hash_algorithm", "SHA-256"),
            signatures=sigs,
            audit=[AuditEvent(**a) for a in d.get("audit", [])],
        )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _text(el, path, default=""):
    found = el.find(path) if el is not None else None
    return found.text.strip() if (found is not None and found.text) else default


def loads(xml: str) -> Contract:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ContractParseError(str(exc)) from exc
    if root.tag != "signingDocument":
        raise ContractParseError(f"root element is <{root.tag}>, expected <signingDocument>")

    c = Contract(id=root.get("id", ""), version=root.get("version", "1.0"))

    meta = root.find("metadata")
    c.title = _text(meta, "title")
    c.doc_type = _text(meta, "documentType") or root.get("type", "")
    c.created_at = _text(meta, "createdAt")
    c.status = _text(meta, "status", "draft")

    for pe in root.findall("./parties/party"):
        party = Party(
            id=pe.get("id", ""), type=pe.get("type", "person"), role=pe.get("role", "signer"),
            legal_name=_text(pe, "legalName"), email=_text(pe, "email"),
        )
        rep = pe.find("representative")
        if rep is not None:
            party.representative = Representative(
                id=rep.get("id", ""), name=_text(rep, "name"),
                title=_text(rep, "title"), email=_text(rep, "email"),
            )
        c.parties.append(party)

    ce = root.find("content")
    if ce is not None:
        c.content = Content(
            id=ce.get("id", "content-1"), media_type=ce.get("mediaType", "text/plain"),
            encoding=ce.get("encoding", "base64"), data=(ce.text or "").strip(),
        )
    che = root.find("contentHash")
    if che is not None:
        c.content_hash = (che.text or "").strip()
        c.content_hash_algorithm = che.get("algorithm", "SHA-256")

    for se in root.findall("./signatures/signature"):
        sig = Signature(
            id=se.get("id", ""), party=se.get("party", ""),
            representative=se.get("representative", ""), target=se.get("target", "content-1"),
            type=se.get("type", "electronic"), status=se.get("status", "completed"),
            signed_at=_text(se, "signedAt"), intent=_text(se, "intent", "approve-and-sign"),
        )
        ap = se.find("appearance")
        if ap is not None:
            sig.appearance = Appearance(
                typed_name=_text(ap, "typedName"), image_b64=_text(ap, "image"),
            )
        es = se.find("electronicSignature")
        if es is not None:
            sig.method = _text(es, "method")
            sig.signed_hash = _text(es, "signedHash")
            sv = es.find("signatureValue")
            if sv is not None:
                sig.signature_value = (sv.text or "").strip()
                sig.signature_algorithm = sv.get("algorithm", "")
            sig.public_key_pem = _text(es, "certificate") or _text(es, "publicKey")
        c.signatures.append(sig)

    for ee in root.findall("./auditTrail/event"):
        c.audit.append(AuditEvent(
            type=ee.get("type", ""), actor=ee.get("actor", ""),
            signature=ee.get("signature", ""), timestamp=ee.get("timestamp", ""),
        ))
    return c


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k: v for k, v in attrs.items() if v not in (None, "")})
    if text not in (None, ""):
        el.text = text
    return el


def dumps(contract: Contract) -> str:
    root = ET.Element("signingDocument", {"id": contract.id or "", "version": contract.version or "1.0"})

    meta = ET.SubElement(root, "metadata")
    _sub(meta, "title", contract.title)
    _sub(meta, "documentType", contract.doc_type)
    _sub(meta, "createdAt", contract.created_at)
    _sub(meta, "status", contract.status or "draft")

    parties = ET.SubElement(root, "parties")
    for p in contract.parties:
        pe = _sub(parties, "party", id=p.id, type=p.type, role=p.role)
        _sub(pe, "legalName", p.legal_name)
        _sub(pe, "email", p.email)
        if p.representative:
            rep = _sub(pe, "representative", id=p.representative.id)
            _sub(rep, "name", p.representative.name)
            _sub(rep, "title", p.representative.title)
            _sub(rep, "email", p.representative.email)

    ce = _sub(root, "content", text=contract.content.data, id=contract.content.id,
              mediaType=contract.content.media_type, encoding=contract.content.encoding)
    if contract.content_hash:
        _sub(root, "contentHash", text=contract.content_hash,
             target=contract.content.id, algorithm=contract.content_hash_algorithm)

    sigs = ET.SubElement(root, "signatures")
    for s in contract.signatures:
        se = _sub(sigs, "signature", id=s.id, party=s.party, representative=s.representative,
                  target=s.target, type=s.type, status=s.status)
        _sub(se, "signedAt", s.signed_at)
        _sub(se, "intent", s.intent)
        if s.appearance and (s.appearance.typed_name or s.appearance.image_b64):
            ap = ET.SubElement(se, "appearance")
            _sub(ap, "typedName", s.appearance.typed_name)
            _sub(ap, "image", text=s.appearance.image_b64, mediaType="image/png", encoding="base64")
        if s.method or s.signature_value:
            es = ET.SubElement(se, "electronicSignature")
            _sub(es, "method", s.method)
            _sub(es, "signedHash", text=s.signed_hash, target=s.target, algorithm="SHA-256")
            _sub(es, "signatureValue", text=s.signature_value,
                 algorithm=s.signature_algorithm or "Ed25519", encoding="base64")
            _sub(es, "publicKey", text=s.public_key_pem, encoding="pem")

    audit = ET.SubElement(root, "auditTrail")
    for a in contract.audit:
        _sub(audit, "event", type=a.type, actor=a.actor, signature=a.signature, timestamp=a.timestamp)

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


# ---------------------------------------------------------------------------
# Builders / mutators
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_contract(title: str = "Untitled contract", issuer_name: str = "", issuer_email: str = "",
                 doc_type: str = "") -> Contract:
    """A blank draft with a single issuer party — used by 'New file' in the vault."""
    return Contract(
        id=f"DOC-{uuid.uuid4().hex[:8].upper()}",
        version="1.0",
        title=title or "Untitled contract",
        doc_type=doc_type,
        created_at=_now_iso(),
        status="draft",
        parties=[Party(id="party-1", type="organization", role="issuer",
                       legal_name=issuer_name, email=issuer_email)],
        content=Content(id="content-1", media_type="text/plain", encoding="text", data=""),
        audit=[AuditEvent(type="created", actor="party-1", timestamp=_now_iso())],
    )


def append_audit(contract: Contract, type: str, actor: str = "", signature: str = "") -> None:
    contract.audit.append(AuditEvent(type=type, actor=actor, signature=signature, timestamp=_now_iso()))


def add_signature(contract: Contract, signature: Signature) -> None:
    contract.signatures.append(signature)
    signed_parties = {s.party for s in contract.signatures if s.status == "completed"}
    signer_parties = {p.id for p in contract.parties if p.role in ("signer", "issuer")}
    contract.status = "signed" if signer_parties and signer_parties <= signed_parties else "partially-signed"
