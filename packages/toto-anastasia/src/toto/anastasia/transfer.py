"""Moving one file between a Vault bucket and a Capsule's files area.

The two directions are NOT symmetrical, and the asymmetry is the whole design:

* **Into a capsule** is a read from the vault and a write into a scratch-ish
  area the user already pays for by reserving it. It costs the platform
  nothing new, so it is metered as nothing new.
* **Out of a capsule** creates a `VaultFile`. That is durable storage on this
  host, and `toto.vault`'s own upload view says why it must not be free:
  *"Same metrics and the same rate card as the gateway upload — this is the
  other door onto one resource, so it must not be the cheap one."* This is a
  THIRD door onto that resource, and the same sentence binds it.

WHY THE SEQUENCE IS COPIED RATHER THAN CALLED
---------------------------------------------
`vault/api_views.py:FileUploadApiView.post` already does quota → funds →
refused-type → scan → persist → record → charge, in that order and for
documented reasons. The obvious move is to extract it and call it from both.

**`toto.vault` lives in `toto-base`, which is pull-only** — an edit there is
reverted by the next subtree pull, so the extraction cannot land. So the order
is reproduced here, in a portal-owned module, and
`tests/test_transfer.py::MeteringParityTests` reads BOTH call sites and
asserts they name the same metrics, the same policy and the same tariff. That
is weaker than one function and it is what the vendor rule allows; the test is
what keeps it honest, and it fails loudly if the vault door ever changes.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No recursive copy, no whole-area tar, no rename. One file at a time, both
ways. A directory walk out of a capsule is `sepulka_restore.py`'s problem
shape — traversal, links, a ratio guard — and the files area already refuses
every one of those per name. Adding a bulk verb would mean re-deciding all of
it at a level where a mistake is a loop.
"""

from __future__ import annotations

import hashlib
import logging
import os
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils.text import slugify

from . import runtime

log = logging.getLogger("toto.anastasia.transfer")

#: The metric names the vault bills a stored file under. Named here so the
#: parity test can compare these against the vault's own door by reading both,
#: rather than by trusting two hand-written lists to stay equal.
STORAGE_REQUEST = "storage.request"
STORAGE_TRANSFER_MB = "storage.transfer_mb"

#: The rate-card key. `price_for(user, "vault")`, exactly as the upload door
#: asks — a different key here would be a different price for the same byte.
TARIFF_KEY = "vault"


#: What a vault title may not contain. A newline makes `FileResponse` raise
#: on every download of the file (it builds a Content-Disposition header from
#: the title); a bidi override spoofs the extension wherever the title is
#: rendered. `VaultFile.title` is 255 wide and Postgres refuses, not truncates.
#: The executor already refuses these in a NAME (`executor/files.py`); this is
#: the same rule for the TITLE, which the client may set freely.
_TITLE_REFUSED = frozenset(chr(c) for c in range(32)) | {"\x7f"} \
    | frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
MAX_TITLE_LENGTH = 255


class TransferRefused(ValidationError):
    """A refusal with a sentence, and a machine-readable code beside it.

    A `ValidationError` so the API's existing `_refusal` renders it with the
    shape every other capsule refusal has, rather than a second format the
    client would have to learn.
    """

    def __init__(self, message: str, *, code: str = ""):
        super().__init__(message)
        self.refusal_code = code


# --------------------------------------------------------------------------- #
# Vault -> Capsule                                                             #
# --------------------------------------------------------------------------- #

def to_capsule(*, lease, vault_file, name: str = "", actor=None,
               replace: bool = False) -> dict:
    """Copy one vault file into the capsule's files area.

    NOT METERED, and that is a decision rather than an omission. Nothing
    durable is created: the bytes land in an area whose capacity the user
    already reserved and already holds, and they die with the reservation. A
    charge here would bill somebody twice for one Capsule.

    `name` defaults to the file's title. It is judged by the executor, not
    here — `executor/files.py:safe_name` borrows `staging`'s rule, and a
    second opinion on this side would be a second rule that could disagree.
    """
    del actor                       # ownership is settled by the caller
    backend = runtime.get_backend()
    writer = getattr(backend, "capsule_file_write", None)
    if writer is None:
        raise TransferRefused(
            "this deployment's runtime cannot hold files in a Capsule",
            code="unsupported")

    target = (name or vault_file.title or "").strip()
    if not target:
        raise TransferRefused("give the file a name inside the Capsule",
                              code="no_name")

    data = _read_vault_bytes(vault_file)
    return {
        **writer(lease, target, data, replace=replace),
        "bytes": len(data),
        "from": {"key": vault_file.key, "title": vault_file.title},
    }


def clean_title(title, *, fallback: str) -> str:
    """The title a VaultFile may carry, or a refusal that says what is wrong.

    REFUSED, NOT REWRITTEN. Quietly dropping a character would store a title
    the person did not type and never see; the sentence lets them type another.
    An empty title falls back to the file's leaf name, as the upload door's
    does.
    """
    final = (title or "").strip() or fallback
    bad = sorted(set(final) & _TITLE_REFUSED)
    if bad:
        raise TransferRefused(
            "the title contains a character a title may not "
            f"({', '.join(repr(c) for c in bad[:3])})", code="bad_title")
    if len(final) > MAX_TITLE_LENGTH:
        raise TransferRefused(
            f"the title is {len(final)} characters; the most is "
            f"{MAX_TITLE_LENGTH}", code="bad_title")
    return final


def _read_vault_bytes(vault_file) -> bytes:
    """The stored bytes, or a refusal naming the file.

    Read whole rather than streamed, because the executor's write takes a
    body: the transfer budget in `executor/capsules.py` is what bounds this,
    and it is checked on the far side where the write actually happens.
    """
    try:
        handle = vault_file.file.open("rb")
    except Exception as exc:        # noqa: BLE001 — a missing blob is a refusal
        raise TransferRefused(
            f"“{vault_file.title}” could not be read from the Vault ({exc})",
            code="unreadable") from None
    try:
        return handle.read()
    finally:
        try:
            handle.close()
        except Exception:           # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# Capsule -> Vault                                                             #
# --------------------------------------------------------------------------- #

def to_bucket(*, lease, name: str, bucket, actor, title: str = "",
              directory=None):
    """Copy one file out of a capsule into a Vault bucket. Returns the VaultFile.

    THE THIRD DOOR ONTO STORAGE, and it charges like the other two. The order
    below is `vault/api_views.py:FileUploadApiView.post`'s, step for step, and
    the reasons are its reasons:

    1. **quota and funds first** — a refusal that costs nothing has nothing to
       refund, and nothing has been created yet;
    2. **the refused-type list** — a host that does not accept a type must not
       acquire one through a side door;
    3. **the antivirus scan, on the bytes, before any row exists** — "nothing
       has been created yet, so a refusal costs no cleanup and leaves no
       half-made row behind". This matters more here than at the upload door:
       these bytes were written by a runner, and a runner may have been
       compromised by the very document it was asked to process;
    4. **a key unique per OWNER**, not per bucket — the vault's detail,
       download, delete and move endpoints look up by key alone, and a
       cross-bucket collision makes those ambiguous;
    5. **record then charge**, with an idempotency key, after the row exists so
       the charge has something to point at.
    """
    from toto.quota import InArrears, QuotaExceeded, check_quota, record_usage
    from toto.quota.charge import (InsufficientFunds, charge, check_funds,
                                   price_for)
    from toto.vault import scanning
    from toto.vault.models import (VaultFile, VaultQuotaPolicy, VaultUsageEvent,
                                   refused_file_types)

    backend = runtime.get_backend()
    reader = getattr(backend, "capsule_file_read", None)
    if reader is None:
        raise TransferRefused(
            "this deployment's runtime cannot read files from a Capsule",
            code="unsupported")

    data = reader(lease, name)
    size = len(data)
    size_mb = Decimal(str(size)) / Decimal("1048576")

    # -- 1. quota and funds, before anything exists -------------------------
    tariff = price_for(actor, TARIFF_KEY)
    try:
        check_quota(VaultQuotaPolicy, STORAGE_REQUEST, 1, actor)
        check_quota(VaultQuotaPolicy, STORAGE_TRANSFER_MB, size_mb, actor)
        check_funds(actor, tariff, STORAGE_REQUEST, 1)
        check_funds(actor, tariff, STORAGE_TRANSFER_MB, size_mb)
    except (QuotaExceeded, InArrears, InsufficientFunds) as exc:
        raise TransferRefused(
            str(exc), code=getattr(exc, "refusal_code", "") or "quota") from exc

    leaf = os.path.basename(name.rstrip("/")) or name
    final_title = clean_title(title, fallback=leaf)
    file_type = VaultFile.detect_type("", leaf)

    # -- 2. a type this host refuses -----------------------------------------
    if file_type in refused_file_types():
        raise TransferRefused(
            f"This host does not accept {file_type} files.",
            code="refused_type")

    # -- 3. the scan, on bytes a RUNNER wrote ---------------------------------
    verdict = None
    if scanning.should_scan(actor, file_type, door="capsule"):
        verdict = scanning.scan(data, file_type=file_type, filename=leaf)
        if not verdict.ok:
            raise TransferRefused(
                verdict.as_error().get("error", "That file was refused."),
                code="infected")

    # -- 4. a key unique per owner -------------------------------------------
    base_key = slugify(final_title) or slugify(leaf) or "file"
    key = base_key
    counter = 1
    while VaultFile.objects.filter(owner=actor, key=key).exists():
        key = f"{base_key}-{counter}"
        counter += 1

    from django.core.files.base import ContentFile

    vault_file = VaultFile(
        owner=actor,
        title=final_title,
        key=key,
        file_type=file_type,
        content_hash=hashlib.sha256(data).hexdigest(),
        file_size_bytes=size,
        bucket=bucket,
        directory=directory,
    )
    vault_file.file.save(leaf, ContentFile(data), save=False)
    vault_file.save()
    if verdict is not None:
        scanning.record(vault_file, verdict, user=actor, door="capsule")

    # -- 5. record, then charge ----------------------------------------------
    src = {"source_type": "vault.VaultFile", "source_id": str(vault_file.pk)}
    record_usage(VaultUsageEvent, STORAGE_REQUEST, 1, actor,
                 idempotency_key=f"anastasia.transfer.request:{vault_file.pk}",
                 **src)
    charge(actor, tariff, STORAGE_REQUEST, 1, **src)
    if size_mb > 0:
        record_usage(VaultUsageEvent, STORAGE_TRANSFER_MB, size_mb, actor,
                     unit="MB",
                     idempotency_key=f"anastasia.transfer.transfer:{vault_file.pk}",
                     **src)
        charge(actor, tariff, STORAGE_TRANSFER_MB, size_mb, unit="MB", **src)
    return vault_file
