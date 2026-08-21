"""One PIN, one action, one request.

This is the door every remote credential goes through. It follows jess's
manual-release mode (``toto/jess/vault.py``), which states the property the whole
model rests on: *there must be no long-lived unlocked session anywhere.*

**It deliberately does NOT follow ``toto/assets/wallet_pin.py``**, which is the
nearest-looking code in the tree and is wrong for this job in five ways:

* its strongbox password is ``HMAC(SECRET_KEY, user.pk)``, so a dump plus
  ``SECRET_KEY`` yields every user's PIN in plaintext;
* it compares a decrypted stored PIN with ``==`` — not constant time, and the
  PIN should never be decryptable at all;
* it **fails open** when no PIN is set;
* it marks a 300-second session flag, so one unlock authorizes everything that
  follows;
* its "single-use-style" token is never consumed.

Here the PIN **is** the strongbox passphrase. There is nothing stored to compare
it against, so "wrong PIN" is "the unwrap raised", and there is no session flag
because there is no state that could be marked — the secret has to be present at
the moment of the unwrap. That is what makes this one-action rather than a
timed global unlock.

## Rate limiting

The codebase has none, and its answer is the notarius economics: charge
immediately before the Argon2id derivation, short-circuit the free cases before
it, and never refund a failed attempt. A wrong PIN spends the same 64 MiB as a
right one, so grinding costs the grinder.
"""

from __future__ import annotations

import json
import secrets
from contextlib import contextmanager

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from .credentials import (
    CredentialEnrollment,
    CredentialWrap,
    RemoteCredential,
    RunCapability,
    fingerprint_for,
)

#: The operator's storage strongbox.
#:
#: The name MUST sort last among a user's strongbox names, and that is not a
#: style choice. ``UserStrongbox.Meta.ordering`` is ``["name"]`` and eight call
#: sites do ``user_strongboxes.first()`` — including the salt every encrypted
#: vault file is sealed under (``vault/strategy/text.py``, ``image.py``) and the
#: KDF record the desktop app derives with (``vault/api_views.py``). A box that
#: sorted earlier would change that salt and make already-encrypted files
#: permanently unopenable. "vault-storage" sorts after every name in the tree
#: today. Do not rename it to "storage".
STORAGE_STRONGBOX_NAME = "vault-storage"

#: How long a queued run's capability may live. Read from the registered stuck-run
#: policies so there is one place to change, and so a capability can never outlive
#: the run it authorizes.
_DEFAULT_TTL_SECONDS = {"refresh": 1800, "transfer": 3600, "probe": 600}


class StoragePinRequired(RuntimeError):
    """No PIN was supplied, or this operator has no storage strongbox yet."""


class StoragePinRejected(RuntimeError):
    """One answer for wrong PIN, missing wrap and corrupt row.

    Telling them apart tells an attacker which of the three they achieved —
    the same reasoning ``gervazy/sealed.py`` gives for its single SealError.
    """


class NotAuthorizedForCredential(StoragePinRejected):
    """This operator holds a PIN, but no wrap on this credential."""


class CapabilityUnavailable(RuntimeError):
    """Spent, expired, or minted for a different run."""


# --------------------------------------------------------------------------- #
# The strongbox                                                                #
# --------------------------------------------------------------------------- #

def storage_strongbox(user):
    """This operator's storage strongbox, or None. Never creates one."""
    from toto.gervazy.models import UserStrongbox

    return UserStrongbox.objects.filter(
        owner=user, name=STORAGE_STRONGBOX_NAME).first()


def has_storage_pin(user) -> bool:
    return storage_strongbox(user) is not None


def set_storage_pin(user, raw_pin: str):
    """Provision this operator's storage strongbox. First time only."""
    from toto.gervazy.crypto import GervazyCryptoSession

    if not raw_pin or len(raw_pin) < 6:
        raise StoragePinRejected("A storage PIN must be at least 6 characters.")
    if has_storage_pin(user):
        raise StoragePinRejected(
            "This account already has a storage PIN. Change it instead.")
    session, _wrapped = GervazyCryptoSession.initialize_strongbox(
        user, STORAGE_STRONGBOX_NAME, raw_pin)
    session.close()


def change_storage_pin(user, old_pin: str, new_pin: str):
    """Re-wrap the master key. No bucket, no credential and no wrap moves.

    ``rewrap_master_keys`` is verify-first (a wrong old PIN changes nothing),
    takes a fresh salt, and re-wraps every VMK inside one transaction. Nothing
    below the VMK is touched, so every ``CredentialWrap`` row is byte-identical
    afterwards and every OTHER operator is entirely unaffected.
    """
    from toto.gervazy.crypto import GervazyCryptoSession

    box = storage_strongbox(user)
    if box is None:
        raise StoragePinRequired("This account has no storage PIN yet.")
    if not new_pin or len(new_pin) < 6:
        raise StoragePinRejected("A storage PIN must be at least 6 characters.")
    try:
        GervazyCryptoSession.rewrap_master_keys(box, old_pin, new_pin)
    except Exception as exc:  # noqa: BLE001 — one answer, see StoragePinRejected
        raise StoragePinRejected("That PIN did not open this strongbox.") from exc


def _open_session(user, raw_pin: str):
    """A FRESH session, never cached. jess's rule, verbatim."""
    from toto.gervazy.crypto import GervazyCryptoSession

    if not raw_pin:
        raise StoragePinRequired("A storage PIN is required for this action.")
    box = storage_strongbox(user)
    if box is None:
        raise StoragePinRequired("This account has no storage PIN yet.")
    return GervazyCryptoSession(box, raw_pin)


# --------------------------------------------------------------------------- #
# Sealing and opening a credential                                             #
# --------------------------------------------------------------------------- #

def _active_wrapped_key(session, user):
    """This operator's active DEK, minting one if the box has none."""
    from toto.gervazy.models import WrappedDataKey

    key = (WrappedDataKey.objects
           .filter(strongbox=session._strongbox, state="active")
           .order_by("-version").first())
    return key or session.create_data_key()


def _wrap_bck(session, credential, user, bck: bytes, *, enrolled_by=None):
    wrapped_key = _active_wrapped_key(session, user)
    wrap = CredentialWrap(
        credential=credential, operator=user, wrapped_key=wrapped_key,
        enrolled_by=enrolled_by or user)
    ciphertext, nonce = session.encrypt_blob(wrapped_key, bck, aad=wrap.build_aad())
    wrap.wrapped_bck, wrap.nonce = ciphertext, nonce
    wrap.save()
    return wrap


def _unwrap_bck(session, wrap) -> bytes:
    """Re-fetch the graph first: a PIN change rewrites the VMK ciphertext in
    place, so any object read before it is stale and fails with InvalidTag."""
    fresh = (CredentialWrap.objects
             .select_related("wrapped_key__vmk", "wrapped_key__strongbox",
                             "credential")
             .get(pk=wrap.pk))
    try:
        return session.decrypt_blob(
            fresh.wrapped_key, bytes(fresh.wrapped_bck), bytes(fresh.nonce),
            aad=fresh.build_aad())
    except Exception as exc:  # noqa: BLE001
        raise StoragePinRejected("That PIN did not open this credential.") from exc


def _seal_payload(credential, bck: bytes, payload: dict):
    from toto.gervazy.crypto import aes_gcm_encrypt

    return aes_gcm_encrypt(bck, json.dumps(payload, sort_keys=True).encode(),
                           credential.build_aad())


def _open_payload(credential, bck: bytes) -> dict:
    from toto.gervazy.crypto import aes_gcm_decrypt

    try:
        raw = aes_gcm_decrypt(bck, bytes(credential.ciphertext),
                              bytes(credential.nonce), credential.build_aad())
    except Exception as exc:  # noqa: BLE001
        raise StoragePinRejected("That PIN did not open this credential.") from exc
    return json.loads(raw.decode())


def _hint_for(kind: str, payload: dict) -> tuple[str, str]:
    """What is safe to show, and what verifies without shortening the search."""
    if kind == "s3":
        # The access key id is not a secret: it travels in every Authorization
        # header and the provider prints it in their console. The SECRET is
        # never shown, not even a prefix.
        return payload.get("aws_access_key_id", ""), fingerprint_for(
            payload.get("aws_secret_access_key", ""))
    api_key = payload.get("api_key", "")
    # Last 8 keeps the exporting host's convention, which is what makes it a
    # cross-host diagnostic rather than a local decoration.
    return api_key[-8:], fingerprint_for(api_key)


@transaction.atomic
def create_credential(*, kind, payload, operator, pin, bucket=None, peer=None):
    """Seal a credential and enroll its first operator, in one transaction."""
    if (bucket is None) == (peer is None):
        raise ValueError("A credential targets exactly one bucket or one peer.")
    session = _open_session(operator, pin)
    try:
        bck = secrets.token_bytes(32)
        credential = RemoteCredential(kind=kind, bucket=bucket, peer=peer,
                                      created_by=operator)
        hint, fingerprint = _hint_for(kind, payload)
        credential.hint, credential.fingerprint = hint, fingerprint
        ciphertext, nonce = _seal_payload(credential, bck, payload)
        credential.ciphertext, credential.nonce = ciphertext, nonce
        credential.save()
        _wrap_bck(session, credential, operator, bck)
        return credential
    finally:
        session.close()


@transaction.atomic
def rotate_credential(credential, *, payload, operator, pin):
    """Re-seal under the SAME BCK. Every other operator's wrap keeps working."""
    session = _open_session(operator, pin)
    try:
        wrap = _require_wrap(credential, operator)
        bck = _unwrap_bck(session, wrap)
        credential.version += 1
        credential.hint, credential.fingerprint = _hint_for(credential.kind, payload)
        ciphertext, nonce = _seal_payload(credential, bck, payload)
        credential.ciphertext, credential.nonce = ciphertext, nonce
        credential.rotated_at = timezone.now()
        credential.save(update_fields=["version", "hint", "fingerprint",
                                       "ciphertext", "nonce", "rotated_at"])
        return credential
    finally:
        session.close()


def _require_wrap(credential, operator):
    wrap = CredentialWrap.objects.filter(
        credential=credential, operator=operator, is_active=True).first()
    if wrap is None:
        raise NotAuthorizedForCredential(
            "This account is not enrolled on that credential.")
    return wrap


@contextmanager
def authorize(operator, pin, credential):
    """Open ONE credential for ONE action, then drop everything.

    Derived: UKEK, VMK, DEK, BCK, plaintext — all in memory, for the duration of
    the ``with`` block only. Persisted: nothing. There is no session flag and no
    bearer token, so there is no unlocked state that could outlive the request.
    """
    session = _open_session(operator, pin)
    try:
        wrap = _require_wrap(credential, operator)
        bck = _unwrap_bck(session, wrap)
        payload = _open_payload(credential, bck)
        wrap.note_use()
        credential.note_use()
        yield payload
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Enrolling a second operator                                                  #
# --------------------------------------------------------------------------- #

@transaction.atomic
def offer_enrollment(credential, invitee, *, operator, pin):
    """Mint a show-once ticket carrying the BCK to one named invitee.

    Returns ``(enrollment, raw_code)``. The code is never stored — only its
    hash and an 8-character hint — so it exists in exactly one place: the
    message that shows it, once.
    """
    from toto.gervazy.crypto import aes_gcm_encrypt

    if CredentialWrap.objects.filter(credential=credential, operator=invitee,
                                     is_active=True).exists():
        raise StoragePinRejected("That account is already enrolled.")
    session = _open_session(operator, pin)
    try:
        wrap = _require_wrap(credential, operator)
        bck = _unwrap_bck(session, wrap)
        etk = secrets.token_bytes(32)
        raw_code = secrets.token_urlsafe(32)
        enrollment = CredentialEnrollment(
            credential=credential, invitee=invitee, offered_by=operator,
            code_hash=make_password(raw_code + _b64(etk)),
            code_hint=raw_code[-8:])
        # The row needs its uid before the AAD can name it.
        enrollment.sealed_bck, enrollment.nonce = aes_gcm_encrypt(
            etk, bck, enrollment.build_aad())
        enrollment.save()
        return enrollment, f"{raw_code}.{_b64(etk)}"
    finally:
        session.close()


def _b64(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    import base64

    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def redeem_enrollment(enrollment_uid, raw_code, *, user, pin):
    """Redeem a ticket: prove the code, then wrap the BCK under this operator.

    Single-use is enforced by the row under ``select_for_update``, so two
    concurrent redemptions cannot both win — the bug ``wallet_pin`` has, where
    the token is never consumed at all.
    """
    from toto.gervazy.crypto import aes_gcm_decrypt

    with transaction.atomic():
        enrollment = (CredentialEnrollment.objects.select_for_update()
                      .select_related("credential")
                      .filter(enrollment_uid=enrollment_uid).first())
        if enrollment is None:
            raise CapabilityUnavailable("No such enrollment.")
        # Cheap checks first: check_password is PBKDF2 at the project's
        # iteration count, and running it before the free checks is how an
        # endpoint becomes a CPU sink.
        if enrollment.invitee_id != user.pk:
            raise CapabilityUnavailable("That enrollment is for another account.")
        if not enrollment.can_be_used:
            raise CapabilityUnavailable(
                "That enrollment has been used or has expired. Ask for a new one.")
        try:
            code_part, etk_part = raw_code.strip().rsplit(".", 1)
        except ValueError:
            raise CapabilityUnavailable("That is not an enrollment code.")
        if not check_password(code_part + etk_part, enrollment.code_hash):
            raise CapabilityUnavailable("That enrollment code is not valid.")

        try:
            bck = aes_gcm_decrypt(_unb64(etk_part), bytes(enrollment.sealed_bck),
                                  bytes(enrollment.nonce), enrollment.build_aad())
        except Exception as exc:  # noqa: BLE001
            raise CapabilityUnavailable("That enrollment code is not valid.") from exc

        session = _open_session(user, pin)
        try:
            wrap = _wrap_bck(session, enrollment.credential, user, bck,
                             enrolled_by=enrollment.offered_by)
        finally:
            session.close()

        # Inert afterwards even if the code leaks later.
        enrollment.redeemed_at = timezone.now()
        enrollment.sealed_bck = b""
        enrollment.nonce = b""
        enrollment.save(update_fields=["redeemed_at", "sealed_bck", "nonce"])
        return wrap


def revoke_operator(credential, operator):
    """One DELETE. No ciphertext moves, no other operator is disturbed."""
    return CredentialWrap.objects.filter(
        credential=credential, operator=operator).delete()


# --------------------------------------------------------------------------- #
# Carrying authority into a queued run                                         #
# --------------------------------------------------------------------------- #

def _run_key() -> str:
    key = getattr(settings, "VAULT_RUN_KEY", "") or ""
    if not key:
        raise CapabilityUnavailable(
            "This host has no VAULT_RUN_KEY, so a sealed bucket cannot queue "
            "work. Set it and restart.")
    return key


def capability_ttl(run_kind: str) -> int:
    return _DEFAULT_TTL_SECONDS.get(run_kind, 600)


def issue_capability(credential, *, run_kind, run_id, operator, pin=None,
                     payload=None):
    """Seal the credential for one run under VAULT_RUN_KEY.

    Pass ``payload`` when the caller already has the plaintext open (inside an
    ``authorize()`` block); otherwise a PIN is required and one is opened here.
    """
    from toto.gervazy import sealed
    from toto.gervazy.models import random_16_byte_salt

    if payload is None:
        with authorize(operator, pin, credential) as opened:
            payload = opened

    capability = RunCapability(
        credential=credential, run_kind=run_kind, run_id=run_id,
        issued_to=operator, salt=random_16_byte_salt(),
        expires_at=timezone.now() + timezone.timedelta(
            seconds=capability_ttl(run_kind)))
    capability.sealed = sealed.seal(
        _run_key(), bytes(capability.salt),
        json.dumps(payload, sort_keys=True).encode(),
        memory_cost=capability.argon2_memory_cost,
        iterations=capability.argon2_iterations,
        lanes=capability.argon2_lanes,
        aad=capability.build_aad())
    capability.save()
    return capability


def consume_capability(capability_uid, *, run_kind, run_id) -> dict:
    """Spend it, then open it. In that order, and under a row lock.

    Consume-before-open is deliberate: a failed open still spends the
    capability, which matches "never refund a failed attempt" and makes a
    decrypt-oracle loop impossible.
    """
    from toto.gervazy import sealed

    with transaction.atomic():
        capability = (RunCapability.objects.select_for_update()
                      .select_related("credential")
                      .filter(capability_uid=capability_uid,
                              run_kind=run_kind, run_id=run_id,
                              consumed_at__isnull=True,
                              expires_at__gt=timezone.now())
                      .first())
        if capability is None:
            raise CapabilityUnavailable(
                "This run's authorization is spent or expired — start it again "
                "and type your PIN.")
        blob = bytes(capability.sealed)
        salt = bytes(capability.salt)
        aad = capability.build_aad()
        costs = (capability.argon2_memory_cost, capability.argon2_iterations,
                 capability.argon2_lanes)
        capability.consumed_at = timezone.now()
        capability.sealed = b""
        capability.save(update_fields=["consumed_at", "sealed"])

    try:
        raw = sealed.open_frame(_run_key(), salt, blob, aad=aad,
                                memory_cost=costs[0], iterations=costs[1],
                                lanes=costs[2])
    except Exception as exc:  # noqa: BLE001
        raise CapabilityUnavailable(
            "This run's authorization could not be opened.") from exc
    return json.loads(raw.decode())


def discard_capabilities(run_kind, run_id):
    """Best-effort cleanup when a run ends. Never raises: a capability tidy-up
    must not be what fails a finished run."""
    try:
        RunCapability.objects.filter(
            run_kind=run_kind, run_id=run_id, consumed_at__isnull=True
        ).update(consumed_at=timezone.now(), sealed=b"")
    except Exception:  # noqa: BLE001
        pass
