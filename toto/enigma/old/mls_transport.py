from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from toto.enigma.models import MlsDeviceSession, Participant, Room


def enigma_models():
    from toto.enigma.models import MlsDeviceSession, Participant, Room

    return MlsDeviceSession, Participant, Room


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"), validate=True)


def bot_session_class():
    try:
        import enigma_py
    except ImportError as exc:
        raise RuntimeError(
            "enigma_py is not installed. Run "
            "`cd studio/enigma/cargo/enigma_py && maturin develop`."
        ) from exc

    return enigma_py.BotSession


@dataclass
class MlsDeviceTransport:
    session: "MlsDeviceSession"
    bot: object

    @staticmethod
    def participant_identity(
        participant: "Participant",
        *,
        device_kind: str,
        device_id: str,
    ) -> str:
        return f"{participant.display_name}#{device_kind}:{device_id}"

    @classmethod
    @transaction.atomic
    def load_or_create(
        cls,
        *,
        room: "Room",
        participant: "Participant",
        device_kind: str,
        device_id: str,
        identity: Optional[str] = None,
    ) -> "MlsDeviceTransport":
        MlsDeviceSession, _Participant, _Room = enigma_models()

        if participant.room_id != room.id:
            raise ValueError("Participant does not belong to the given room.")

        resolved_identity = identity or cls.participant_identity(
            participant,
            device_kind=device_kind,
            device_id=device_id,
        )

        session, _created = MlsDeviceSession.objects.select_for_update().get_or_create(
            room=room,
            participant=participant,
            device_kind=device_kind,
            device_id=device_id,
            defaults={
                "identity": resolved_identity,
                "state": None,
                "is_joined": False,
                "last_error": "",
            },
        )

        BotSession = bot_session_class()

        if session.state:
            bot = BotSession.import_state(bytes(session.state))
        else:
            bot = BotSession(room.slug, session.identity)
            session.state = bytes(bot.export_state())
            session.last_error = ""
            session.save(update_fields=["state", "last_error", "updated_at"])

        return cls(session=session, bot=bot)

    @classmethod
    def for_agent(cls, participant: "Participant") -> "MlsDeviceTransport":
        MlsDeviceSession, _Participant, _Room = enigma_models()

        if not participant.agent_id:
            raise ValueError("for_agent() requires an agent participant.")

        return cls.load_or_create(
            room=participant.room,
            participant=participant,
            device_kind=MlsDeviceSession.DEVICE_KIND_AGENT,
            device_id=f"agent:{participant.pk}",
            identity=participant.display_name,
        )

    @classmethod
    def for_agent_peer(
            cls,
            participant: "Participant",
            *,
            peer_device_id: str,
    ) -> "MlsDeviceTransport":
        MlsDeviceSession, _Participant, _Room = enigma_models()

        if not participant.agent_id:
            raise ValueError("for_agent_peer() requires an agent participant.")

        if not peer_device_id:
            raise ValueError("peer_device_id is required.")

        return cls.load_or_create(
            room=participant.room,
            participant=participant,
            device_kind=MlsDeviceSession.DEVICE_KIND_AGENT,
            device_id=f"agent:{participant.pk}:peer:{peer_device_id}",
            identity=f"{participant.display_name}#peer:{peer_device_id}",
        )

    @classmethod
    def for_browser(
        cls,
        *,
        room: "Room",
        participant: "Participant",
        device_id: str,
    ) -> "MlsDeviceTransport":
        MlsDeviceSession, _Participant, _Room = enigma_models()

        return cls.load_or_create(
            room=room,
            participant=participant,
            device_kind=MlsDeviceSession.DEVICE_KIND_BROWSER,
            device_id=device_id,
        )

    @property
    def is_joined(self) -> bool:
        return self.session.is_joined

    @property
    def uuid(self):
        return self.session.uuid

    @property
    def room(self) -> "Room":
        return self.session.room

    @property
    def participant(self) -> "Participant":
        return self.session.participant

    @property
    def identity(self) -> str:
        return self.session.identity

    @staticmethod
    def b64e(data: bytes) -> str:
        return b64e(data)

    @staticmethod
    def b64d(data: str) -> bytes:
        return b64d(data)

    def _exception_message(self, exc: Exception) -> str:
        return str(exc).strip() or exc.__class__.__name__

    def _persist_error(self, exc: Exception) -> None:
        self.session.last_error = self._exception_message(exc)
        self.session.save(update_fields=["last_error", "updated_at"])

    def _save_state(self, *, is_joined: Optional[bool] = None) -> None:
        self.session.state = bytes(self.bot.export_state())
        self.session.last_error = ""

        update_fields = ["state", "last_error", "updated_at"]

        if is_joined is not None:
            self.session.is_joined = is_joined
            update_fields.append("is_joined")

            if is_joined and self.session.joined_at is None:
                self.session.joined_at = timezone.now()
                update_fields.append("joined_at")

        self.session.save(update_fields=update_fields)

    def create_group(self) -> None:
        try:
            self.bot.create_group()
            self._save_state(is_joined=True)
        except Exception as exc:
            self._persist_error(exc)
            raise

    def create_key_package(self, *, force: bool = False) -> bytes:
        """
        Generate and persist an MLS KeyPackage.

        Important:
        If a device is not joined and already has a pending KeyPackage, reuse it.
        Generating a fresh KeyPackage can make an in-flight browser Welcome refer
        to an older key package whose private key is no longer usable.
        """

        if (
            not force
            and not self.session.is_joined
            and self.session.latest_key_package
        ):
            return bytes(self.session.latest_key_package)

        try:
            key_package = bytes(self.bot.key_package())

            self.session.latest_key_package = key_package
            self.session.key_package_created_at = timezone.now()
            self.session.state = bytes(self.bot.export_state())
            self.session.last_error = ""
            self.session.save(
                update_fields=[
                    "latest_key_package",
                    "key_package_created_at",
                    "state",
                    "last_error",
                    "updated_at",
                ]
            )

            return key_package

        except Exception as exc:
            self._persist_error(exc)
            raise

    def key_package_payload(self, participant: Optional["Participant"] = None, *, target: str = "") -> dict:
        participant = participant or self.session.participant
        key_package = self.create_key_package()

        return {
            "type": "mls_key_package",
            "id": str(uuid.uuid4()),
            "user": participant.display_name,
            "avatar_url": participant.avatar_url,
            "participant_type": participant.participant_type,
            "target": target,
            "package": b64e(key_package),
            "device_id": self.session.device_id,
            "device_kind": self.session.device_kind,
            "sender_channel": f"mls:{self.session.device_kind}:{self.session.pk}",
            "target_channel": None,
        }

    def add_member(self, key_package: bytes) -> tuple[bytes, bytes]:
        try:
            welcome, commit = self.bot.add_member(key_package)
            self._save_state()
            return bytes(welcome), bytes(commit)

        except Exception as exc:
            self._persist_error(exc)
            raise

    def add_member_b64(self, key_package_b64: str) -> tuple[str, str]:
        welcome, commit = self.add_member(b64d(key_package_b64))
        return b64e(welcome), b64e(commit)

    def add_member_payload(self, key_package_b64: str, *, target: str) -> dict:
        welcome_b64, commit_b64 = self.add_member_b64(key_package_b64)
        participant = self.session.participant

        return {
            "type": "mls_welcome",
            "id": str(uuid.uuid4()),
            "user": participant.display_name,
            "avatar_url": participant.avatar_url,
            "participant_type": participant.participant_type,
            "target": target,
            "welcome": welcome_b64,
            "commit": commit_b64,
            "device_id": self.session.device_id,
            "device_kind": self.session.device_kind,
            "sender_channel": f"mls:{self.session.device_kind}:{self.session.pk}",
            "target_channel": None,
        }

    def join_from_welcome(self, welcome: bytes) -> None:
        try:
            self.bot.join_from_welcome(welcome)
            self._save_state(is_joined=True)
        except Exception as exc:
            self._persist_error(exc)
            raise

    def join_from_welcome_b64(self, welcome_b64: str) -> None:
        self.join_from_welcome(b64d(welcome_b64))

    def process_message(self, message: bytes) -> Optional[bytes]:
        try:
            result = self.bot.process_message(message)
            self._save_state()

            if result is None:
                return None

            return bytes(result)

        except Exception as exc:
            self._persist_error(exc)
            raise

    def process_message_b64(self, message_b64: str) -> Optional[bytes]:
        return self.process_message(b64d(message_b64))

    def encrypt_app(self, plaintext: bytes) -> bytes:
        try:
            ciphertext = bytes(self.bot.encrypt_app(plaintext))
            if not ciphertext:
                raise RuntimeError("enigma_py returned empty encrypted application message")

            self._save_state()
            return ciphertext

        except Exception as exc:
            self._persist_error(exc)
            raise

    def encrypt_app_b64(self, plaintext: bytes) -> str:
        return b64e(self.encrypt_app(plaintext))

    def encrypt_text_b64(self, text: str) -> str:
        return self.encrypt_app_b64(text.encode("utf-8"))

    def reset(self) -> None:
        BotSession = bot_session_class()
        self.bot = BotSession(self.session.room.slug, self.session.identity)

        self.session.state = bytes(self.bot.export_state())
        self.session.latest_key_package = None
        self.session.key_package_created_at = None
        self.session.is_joined = False
        self.session.joined_at = None
        self.session.last_error = ""
        self.session.save(
            update_fields=[
                "state",
                "latest_key_package",
                "key_package_created_at",
                "is_joined",
                "joined_at",
                "last_error",
                "updated_at",
            ]
        )