# enigma/connection.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from enigma_py import EnigmaEngine

from toto.enigma.models import KeyPackage, Participant, ProcessedMessage, Room, State


def encrypt_state(state: bytes) -> bytes:
    """
    Replace with real encryption-at-rest before production.
    """
    return state


def decrypt_state(encrypted_state: bytes) -> bytes:
    """
    Replace with real decryption-at-rest before production.
    """
    return encrypted_state


@dataclass(frozen=True)
class IncomingResult:
    plaintext: bytes
    message_id: str


@dataclass(frozen=True)
class AddMemberResult:
    welcome: bytes
    commit: bytes


class EnigmaConnection:
    """
    Django-side encrypted MLS connection for one AI participant.

    Stores:
      - encrypted MLS state
      - public KeyPackages
      - processed message IDs for idempotency

    Does not store:
      - plaintext messages
      - ciphertext messages
      - LLM prompts
      - LLM responses
      - chat history
    """

    def __init__(self, *, state_id):
        self.state_id = state_id

    @classmethod
    def for_state(cls, state: State) -> "EnigmaConnection":
        return cls(state_id=state.id)

    @classmethod
    def for_participant(cls, participant: Participant) -> "EnigmaConnection":
        if not participant.agent_id:
            raise ValueError("EnigmaConnection can only be created for an agent participant.")

        state = State.objects.get(participant=participant)
        return cls(state_id=state.id)

    @classmethod
    def for_agent(cls, *, room: Room, agent) -> "EnigmaConnection":
        participant = Participant.objects.get(
            room=room,
            agent=agent,
            is_active=True,
        )
        return cls.for_participant(participant)

    @classmethod
    def create_group_for_agent(cls, *, room: Room, agent) -> "EnigmaConnection":
        participant, _ = Participant.objects.get_or_create(
            room=room,
            agent=agent,
            defaults={"is_active": True},
        )

        if not participant.agent_id:
            raise ValueError("Participant must be an agent.")

        identity = cls.identity_for_participant(participant)

        raw_state = bytes(
            EnigmaEngine.create_group_state(
                room.slug,
                identity,
            )
        )

        with transaction.atomic():
            state, created = State.objects.select_for_update().get_or_create(
                participant=participant,
                defaults={
                    "encrypted_state": encrypt_state(raw_state),
                    "status": State.Status.ACTIVE,
                    "last_error": "",
                },
            )

            if not created:
                state.encrypted_state = encrypt_state(raw_state)
                state.state_version += 1
                state.status = State.Status.ACTIVE
                state.last_error = ""
                state.save(
                    update_fields=[
                        "encrypted_state",
                        "state_version",
                        "status",
                        "last_error",
                        "updated_at",
                    ]
                )

        return cls(state_id=state.id)

    @classmethod
    def create_joining_for_agent(cls, *, room: Room, agent) -> "EnigmaConnection":
        participant, _ = Participant.objects.get_or_create(
            room=room,
            agent=agent,
            defaults={"is_active": True},
        )

        if not participant.agent_id:
            raise ValueError("Participant must be an agent.")

        identity = cls.identity_for_participant(participant)

        raw_state = bytes(
            EnigmaEngine.create_empty_state(
                room.slug,
                identity,
            )
        )

        with transaction.atomic():
            state, created = State.objects.select_for_update().get_or_create(
                participant=participant,
                defaults={
                    "encrypted_state": encrypt_state(raw_state),
                    "status": State.Status.EMPTY,
                    "last_error": "",
                },
            )

            if not created:
                state.encrypted_state = encrypt_state(raw_state)
                state.state_version += 1
                state.status = State.Status.EMPTY
                state.last_error = ""
                state.save(
                    update_fields=[
                        "encrypted_state",
                        "state_version",
                        "status",
                        "last_error",
                        "updated_at",
                    ]
                )

        return cls(state_id=state.id)

    def create_key_package(self, *, expires_at=None) -> KeyPackage:
        with transaction.atomic():
            state = self._locked_state()
            raw_state = decrypt_state(bytes(state.encrypted_state))

            try:
                new_state, public_key_package = EnigmaEngine.key_package_from_state(
                    raw_state,
                )
            except Exception as exc:
                self._mark_error(state, exc)
                raise

            state.encrypted_state = encrypt_state(bytes(new_state))
            state.state_version += 1
            state.status = State.Status.PENDING_WELCOME
            state.last_error = ""
            state.save(
                update_fields=[
                    "encrypted_state",
                    "state_version",
                    "status",
                    "last_error",
                    "updated_at",
                ]
            )

            return KeyPackage.objects.create(
                state=state,
                public_key_package=bytes(public_key_package),
                expires_at=expires_at,
            )

    def join_from_welcome(
        self,
        *,
        welcome: bytes,
        key_package: Optional[KeyPackage] = None,
    ) -> None:
        with transaction.atomic():
            state = self._locked_state()

            if key_package is not None and key_package.state_id != state.id:
                raise ValueError("KeyPackage does not belong to this connection state.")

            raw_state = decrypt_state(bytes(state.encrypted_state))

            try:
                new_state = EnigmaEngine.join_from_welcome_from_state(
                    raw_state,
                    welcome,
                )
            except Exception as exc:
                self._mark_error(state, exc)
                raise

            state.encrypted_state = encrypt_state(bytes(new_state))
            state.state_version += 1
            state.status = State.Status.ACTIVE
            state.last_error = ""
            state.save(
                update_fields=[
                    "encrypted_state",
                    "state_version",
                    "status",
                    "last_error",
                    "updated_at",
                ]
            )

            if key_package is not None and key_package.used_at is None:
                key_package.used_at = timezone.now()
                key_package.save(update_fields=["used_at"])

    def add_member(self, *, key_package: bytes) -> AddMemberResult:
        with transaction.atomic():
            state = self._locked_state()
            self._ensure_active(state)

            raw_state = decrypt_state(bytes(state.encrypted_state))

            try:
                new_state, welcome, commit = EnigmaEngine.add_member_from_state(
                    raw_state,
                    key_package,
                )
            except Exception as exc:
                self._mark_error(state, exc)
                raise

            state.encrypted_state = encrypt_state(bytes(new_state))
            state.state_version += 1
            state.status = State.Status.ACTIVE
            state.last_error = ""
            state.save(
                update_fields=[
                    "encrypted_state",
                    "state_version",
                    "status",
                    "last_error",
                    "updated_at",
                ]
            )

            return AddMemberResult(
                welcome=bytes(welcome),
                commit=bytes(commit),
            )

    def receive(self, *, message_id: str, ciphertext: bytes) -> Optional[IncomingResult]:
        with transaction.atomic():
            state = self._locked_state()
            self._ensure_active(state)

            if ProcessedMessage.objects.filter(
                state=state,
                message_id=message_id,
            ).exists():
                return None

            raw_state = decrypt_state(bytes(state.encrypted_state))

            try:
                new_state, plaintext = EnigmaEngine.process_message_from_state(
                    raw_state,
                    ciphertext,
                )
            except Exception as exc:
                self._mark_error(state, exc)
                raise

            state.encrypted_state = encrypt_state(bytes(new_state))
            state.state_version += 1
            state.last_error = ""
            state.save(
                update_fields=[
                    "encrypted_state",
                    "state_version",
                    "last_error",
                    "updated_at",
                ]
            )

            try:
                ProcessedMessage.objects.create(
                    state=state,
                    message_id=message_id,
                )
            except IntegrityError:
                return None

        if plaintext is None:
            return None

        return IncomingResult(
            plaintext=bytes(plaintext),
            message_id=message_id,
        )

    def encrypt(self, plaintext: bytes) -> bytes:
        with transaction.atomic():
            state = self._locked_state()
            self._ensure_active(state)

            raw_state = decrypt_state(bytes(state.encrypted_state))

            try:
                new_state, ciphertext = EnigmaEngine.encrypt_app_from_state(
                    raw_state,
                    plaintext,
                )
            except Exception as exc:
                self._mark_error(state, exc)
                raise

            state.encrypted_state = encrypt_state(bytes(new_state))
            state.state_version += 1
            state.last_error = ""
            state.save(
                update_fields=[
                    "encrypted_state",
                    "state_version",
                    "last_error",
                    "updated_at",
                ]
            )

            return bytes(ciphertext)

    @property
    def state(self) -> State:
        return (
            State.objects
            .select_related("participant", "participant__room", "participant__agent")
            .get(id=self.state_id)
        )

    @property
    def participant(self) -> Participant:
        return self.state.participant

    @property
    def room(self) -> Room:
        return self.participant.room

    def _locked_state(self) -> State:
        return (
            State.objects
            .select_for_update()
            .select_related("participant", "participant__room", "participant__agent")
            .get(id=self.state_id)
        )

    @staticmethod
    def _ensure_active(state: State) -> None:
        if state.status != State.Status.ACTIVE:
            raise RuntimeError(
                f"EnigmaConnection state is not active; current status is {state.status!r}."
            )

    @staticmethod
    def _mark_error(state: State, exc: Exception) -> None:
        state.status = State.Status.ERROR
        state.last_error = str(exc)
        state.save(update_fields=["status", "last_error", "updated_at"])

    @staticmethod
    def identity_for_participant(participant: Participant) -> str:
        if not participant.agent_id:
            raise ValueError("Only agent participants can have an EnigmaConnection.")

        return f"agent:{participant.agent_id}:{participant.agent.name}"