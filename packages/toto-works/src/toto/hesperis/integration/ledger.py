"""The seam between a Dataset and its chain.

`toto.ledger` knows nothing about datasets — it takes a `scope_type` and a
`scope_uid` and never looks them up. This module is the only place that knows
those two strings mean a Dataset, exactly as `toto.company.integration.ledger`
is the only place they mean a Company.

**The dependency is SOFT, and it has to be.** `toto.hesperis` ships in the
vendored suite; `toto.ledger` is host-owned (`zenobia/zenobia/toto/ledger`). A
wheel that hard-imported a host app would refuse to install anywhere else, so
every import here is lazy and behind `available()`. Where the chain is absent
Hesperis behaves exactly as it did before: observations are accepted, versions
are frozen, and nothing is recorded.

**Why a chain when a version already has a manifest hash.** The manifest hash
answers "does this release still hold the rows it was frozen with". It cannot
answer "was this release ever changed", because whoever could edit the members
could recompute the hash and store it. The chain is append-only, hash-linked and
guarded by database triggers, so a rewrite has to survive `verify()` for every
later block as well — and a QR checkpoint puts the head somewhere nobody can
reach at all.

**What the chain records that the manifest does not: CONTENT.**
`DatasetVersion.compute_manifest_hash` digests member *uids* only, so editing an
accepted observation's payload after a release is frozen leaves `verify()`
returning True. The observation block below carries a digest of the payload as
it stood at acceptance, which is what makes an edit detectable at all.
"""

from __future__ import annotations

import hashlib
import json

from django.apps import apps
from django.db import transaction

#: How a chain names a Dataset. A string, never an import — the ledger app must
#: keep working on a host where Hesperis is not installed.
SCOPE_TYPE = "hesperis.dataset"

#: One chain per dataset. One key, because a dataset has one history.
CHAIN_KEY = "dataset-history"

#: What a block says it is. Read by the validator, so they are constants rather
#: than literals scattered through the append calls.
SOURCE_VERSION = "hesperis.datasetversion"
SOURCE_OBSERVATION = "hesperis.acceptedobservation"


def available() -> bool:
    """Is there a chain engine on this host at all?"""
    return apps.is_installed("toto.ledger")


def payload_digest(value) -> str:
    """SHA-256 over an observation payload, canonically ordered.

    `sort_keys` is what makes this reproducible: the same mapping must digest
    the same way whether it arrived from a form, a fixture or a restore.
    """
    text = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dataset_ledger(dataset, *, actor=None):
    """This dataset's chain, opening it with its genesis block if new.

    Idempotent, so it is safe from a view, a backfill or a test without anyone
    tracking whether it has run. Returns None where the engine is absent.
    """
    if not available():
        return None
    from toto.ledger.models import LedgerKind
    from toto.ledger.services import chain

    return chain.open_ledger(
        key=CHAIN_KEY,
        name=f"{dataset.name} — dataset history",
        kind=LedgerKind.GENERIC,
        scope_type=SCOPE_TYPE,
        scope_uid=dataset.uid,
        description=(
            "Every accepted observation and every frozen release of this "
            "dataset, in the order they happened."
        ),
        actor=actor,
    )


def _datasets_for(observation):
    """Every dataset an observation belongs to.

    An observation is accepted against a BOUNTY; datasets are named per
    campaign. So the datasets that should hear about it are the ones under the
    campaign its bounty belongs to.
    """
    from ..models import Dataset

    campaign_id = observation.bounty.mission.campaign_id
    return Dataset.objects.filter(campaign__campaign_id=campaign_id)


def record_observation(observation, *, actor=None) -> list:
    """Append one block per dataset for a claim that became a fact.

    Recorded at ACCEPTANCE, not at freeze: the point of the block is to fix what
    the observation said at the moment reviewers let it through, so a later edit
    to the payload has something to disagree with.
    """
    if not available():
        return []
    from toto.ledger.services import chain

    written = []
    for dataset in _datasets_for(observation):
        ledger = dataset_ledger(dataset, actor=actor)
        if ledger is None:
            continue
        written.append(chain.append(
            ledger=ledger,
            payload={
                "event": "observation-accepted",
                "observation_uid": str(observation.uid),
                "submission_uid": str(observation.submission.uid),
                "bounty_uid": str(observation.bounty.uid),
                "observed_by": observation.observed_by.display_name(),
                "observed_at": observation.observed_at,
                "accepted_at": observation.accepted_at,
                "payload_digest": payload_digest(observation.payload),
            },
            actor=actor,
            occurred_at=observation.accepted_at,
            source_type=SOURCE_OBSERVATION,
            source_uid=observation.uid,
            source_ref=str(observation.pk),
        ))
    return written


def record_version(version, *, actor=None):
    """Append the block that says a release was published.

    Carries the manifest hash and the member count, so the chain fixes both what
    the release contained and how many rows that was — a release that quietly
    lost a row fails on the count before anyone recomputes anything.
    """
    if not available():
        return None
    from toto.ledger.services import chain

    ledger = dataset_ledger(version.dataset, actor=actor)
    if ledger is None:
        return None
    return chain.append(
        ledger=ledger,
        payload={
            "event": "version-frozen",
            "version_uid": str(version.uid),
            "number": version.number,
            "manifest_hash": version.manifest_hash,
            "member_count": version.members.count(),
            "notes": version.notes,
            "frozen_at": version.frozen_at,
        },
        actor=actor,
        occurred_at=version.frozen_at,
        source_type=SOURCE_VERSION,
        source_uid=version.uid,
        source_ref=str(version.pk),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _source_validator(entry):
    """Check a block against the row it describes. Returns a reason, or None.

    `chain.verify` already proves the chain is internally sound — sequence,
    previous hash, block hash, one genesis. That is a statement about the chain
    ALONE, and it stays true after somebody edits the observation a block
    describes. This is the other half: does the world still match the record?

    A MISSING row is not a failure. Observations and releases are protected
    against deletion, but a dataset may be dropped and its blocks must remain
    verifiable afterwards — a chain that broke when its subject was tidied away
    would be evidence of nothing.
    """
    from ..models import AcceptedObservation, DatasetVersion

    field = _block_field(entry)

    if entry.source_type == SOURCE_OBSERVATION:
        observation = AcceptedObservation.objects.filter(
            uid=entry.source_uid).first()
        if observation is None:
            return None
        recorded = field("payload_digest")
        if recorded and recorded != payload_digest(observation.payload):
            return ("This observation's payload has changed since it was "
                    "accepted.")
        return None

    if entry.source_type == SOURCE_VERSION:
        version = DatasetVersion.objects.filter(uid=entry.source_uid).first()
        if version is None:
            return None
        recorded = field("manifest_hash")
        if recorded and recorded != version.manifest_hash:
            return "This release's manifest hash has changed since it was frozen."
        count = field("member_count")
        if count and str(version.members.count()) != count:
            return "This release holds a different number of rows than it was frozen with."
        return None

    return None


def _block_field(entry):
    """Read one value out of a block's canonical XML.

    The payload is stored as text, deliberately: it is what the hash was taken
    over. Parsing it back is a read, and a malformed block is a verification
    failure rather than an exception — so this returns "" and lets the caller's
    comparison fail loudly.
    """
    import xml.etree.ElementTree as ET

    def read(key: str) -> str:
        try:
            root = ET.fromstring(entry.payload_xml)
        except ET.ParseError:
            return ""
        node = root.find(f".//{key}")
        return (node.text or "") if node is not None else ""

    return read


def verify_dataset(dataset):
    """Walk this dataset's chain and check it against the live rows.

    Returns the engine's own `Verification`, or None where no chain engine is
    installed — which a page must render as "not available", never as "broken".
    """
    if not available():
        return None
    from toto.ledger.services import chain

    ledger = _existing_ledger(dataset)
    if ledger is None:
        return None
    return chain.verify(ledger, source_validator=_source_validator)


def _existing_ledger(dataset):
    """This dataset's chain if it has one — WITHOUT opening a new one.

    `dataset_ledger` creates on demand, which is right when recording and wrong
    when merely looking: rendering a page must not write a genesis block.
    """
    if not available():
        return None
    from toto.ledger.models import Ledger

    return Ledger.objects.filter(
        scope_type=SCOPE_TYPE, scope_uid=dataset.uid, key=CHAIN_KEY).first()
