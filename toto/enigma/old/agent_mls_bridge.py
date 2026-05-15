from __future__ import annotations
import uuid
from dataclasses import dataclass
from enigma.old.mls_transport import MlsDeviceTransport
from toto.steven.services.agent_session import create_agent_session


MLS_TYPES = {
    "mls_key_package",
    "mls_welcome",
    "mls_commit",
    "mls_app",
}


@dataclass
class AgentMlsBridge:
    room_slug: str
    payload: dict

    def handle(self) -> list[dict]:
        """
        Entrypoint called by ChatConsumer.

        Receives already-enriched websocket payloads.
        Returns payloads to broadcast back into the room.
        """

        if self.is_agent_payload(self.payload):
            return []

        payload_type = self.payload.get("type")

        if payload_type not in MLS_TYPES:
            return []

        replies: list[dict] = []

        for participant in self.get_encrypted_agent_participants():
            replies.extend(
                self.handle_agent_mls_payload(
                    participant=participant,
                    payload=self.payload,
                )
            )

        return replies

    @classmethod
    def handle_room_payload(cls, *, room_slug: str, payload: dict) -> list[dict]:
        return cls(room_slug=room_slug, payload=payload).handle()

    @staticmethod
    def system_info_payload(message: str) -> dict:
        return {
            "type": "system_info",
            "user": "System",
            "message": message,
            "sender_channel": "system:mls",
            "target_channel": None,
        }

    @staticmethod
    def system_error_payload(message: str) -> dict:
        return {
            "type": "system_error",
            "user": "System",
            "message": message,
            "sender_channel": "system:mls",
            "target_channel": None,
        }

    @staticmethod
    def is_agent_payload(payload: dict) -> bool:
        return (
            payload.get("participant_type") == "ai_agent"
            or (payload.get("sender_channel") or "").startswith("mls:agent:")
            or (payload.get("sender_channel") or "").startswith("steven_agent:")
        )

    def get_encrypted_agent_participants(self):
        from toto.enigma.models import Room

        room = Room.objects.get(slug=self.room_slug)

        return (
            room.chat_participants.filter(
                is_active=True,
                agent__isnull=False,
                agent__is_active=True,
                agent__uses_encrypted_chat=True,
            )
            .select_related("room", "agent", "agent__connector")
        )

    @staticmethod
    def tiny_agent_reply(*, participant, sender_name: str, message: str) -> str:
        """
        Temporary tiny agent.

        Later replace this with your real Steven AgentRun path.
        """
        return f"{participant.display_name} heard {sender_name}: {message}"

    @staticmethod
    def agent_reply(*, participant, sender_name: str, message: str) -> str:
        """
        Run the real Steven agent session for an encrypted chat message.
        """
        profile = participant.agent

        if not profile:
            raise RuntimeError("Participant has no linked agent.")

        if not profile.is_active:
            raise RuntimeError(f'Agent "{profile.name}" is inactive.')

        user_prompt = f"{sender_name} says:\n\n{message}"

        return create_agent_session(profile).invoke(user_prompt)

    def handle_agent_mls_payload(self, *, participant, payload: dict) -> list[dict]:
        peer_device_id = self.peer_device_id_from_payload(payload)

        if not peer_device_id:
            return [
                self.system_error_payload(
                    f"{participant.display_name} could not handle MLS payload because browser device_id/mls_session_id is missing."
                )
            ]

        transport = MlsDeviceTransport.for_agent_peer(
            participant,
            peer_device_id=peer_device_id,
        )
        payload_type = payload.get("type")

        if payload_type == "mls_key_package":
            # Browser announced its key package. For now, agent does not add browser;
            # browser is expected to add the agent after receiving agent key package.
            return []

        if payload_type == "mls_welcome":
            return self.handle_welcome_payload(
                participant=participant,
                transport=transport,
                payload=payload,
            )

        if payload_type == "mls_commit":
            return self.handle_commit_payload(
                transport=transport,
                payload=payload,
            )

        if payload_type == "mls_app":
            return self.handle_app_payload(
                participant=participant,
                transport=transport,
                payload=payload,
            )

        return []

    def handle_welcome_payload(self, *, participant, transport: MlsDeviceTransport, payload: dict) -> list[dict]:
        target = payload.get("target")
        welcome = payload.get("welcome")

        if target != participant.display_name:
            return []

        target_device_id = payload.get("target_device_id")
        target_device_kind = payload.get("target_device_kind")

        if target_device_id and target_device_id != transport.session.device_id:
            return []

        if target_device_kind and target_device_kind != transport.session.device_kind:
            return []

        if transport.is_joined:
            return []

        if not welcome:
            return [
                self.system_error_payload(
                    f"{participant.display_name} received MLS welcome without welcome bytes."
                )
            ]

        try:
            transport.join_from_welcome_b64(welcome)
        except Exception as exc:
            return [
                self.system_error_payload(
                    f"{participant.display_name} could not join encrypted chat: {exc}"
                )
            ]

        return [
            self.system_info_payload(
                f"{participant.display_name} joined encrypted chat."
            )
        ]

    def handle_commit_payload(self, *, transport: MlsDeviceTransport, payload: dict) -> list[dict]:
        if not transport.is_joined:
            return []

        commit = payload.get("commit")
        if not commit:
            return []

        try:
            transport.process_message_b64(commit)
        except Exception:
            # Some add-member commits may already be reflected by Welcome.
            # Do not spam UI unless app messages fail.
            return []

        return []

    def peer_device_id_from_payload(self, payload: dict) -> str | None:
        device_kind = payload.get("device_kind")
        device_id = payload.get("device_id")
        mls_session_id = payload.get("mls_session_id")

        if device_kind != "browser":
            return None

        if not device_id:
            return None

        if not mls_session_id:
            return None

        return f"{device_id}:session:{mls_session_id}"

    def handle_app_payload(self, *, participant, transport: MlsDeviceTransport, payload: dict) -> list[dict]:
        ciphertext = payload.get("ciphertext")
        if not ciphertext:
            return []

        if not transport.is_joined:
            return self.request_agent_join(
                participant=participant,
                transport=transport,
            )

        try:
            plaintext = transport.process_message_b64(ciphertext)
        except Exception as exc:
            message = str(exc)

            if (
                    "AEAD" in message
                    or "Generation is too old" in message
                    or "generation" in message.lower()
                    or "epoch differs" in message.lower()
                    or "group's epoch" in message.lower()
            ):
                try:
                    transport.reset()
                    return [
                        transport.key_package_payload(participant),
                        self.system_info_payload(
                            f"{participant.display_name} reset encrypted state for this browser. "
                            "Send the message again after the welcome is processed."
                        ),
                    ]
                except Exception as reset_exc:
                    return [
                        self.system_error_payload(
                            f"{participant.display_name} could not reset encrypted state: {reset_exc}"
                        )
                    ]

            return [
                self.system_error_payload(
                    f"{participant.display_name} could not decrypt encrypted message: {exc}"
                )
            ]

        if plaintext is None:
            return []

        sender_name = payload.get("user") or "Someone"
        message = plaintext.decode("utf-8", errors="replace")

        try:
            response = self.agent_reply(
                participant=participant,
                sender_name=sender_name,
                message=message,
            )
        except Exception as exc:
            return [
                self.system_error_payload(
                    f"{participant.display_name} could not run agent: {exc}"
                )
            ]

        try:
            response_ciphertext = transport.encrypt_text_b64(response)
        except Exception as exc:
            return [
                self.system_error_payload(
                    f"{participant.display_name} could not encrypt response: {exc}"
                )
            ]

        return [
            {
                "type": "mls_app",
                "id": str(uuid.uuid4()),
                "user": participant.display_name,
                "avatar_url": participant.avatar_url,
                "participant_type": "ai_agent",
                "agent_slug": participant.agent.slug,
                "ciphertext": response_ciphertext,
                "sender_channel": f"mls:agent:{participant.pk}",
                "target_channel": None,
            }
        ]

    def request_agent_join(self, *, participant, transport: MlsDeviceTransport) -> list[dict]:
        try:
            return [
                transport.key_package_payload(participant),
                self.system_info_payload(
                    f"{participant.display_name} is joining encrypted chat. "
                    "Send the message again after the welcome is processed."
                ),
            ]
        except Exception as exc:
            return [
                self.system_error_payload(
                    f"{participant.display_name} could not create MLS key package: {exc}"
                )
            ]


def handle_agent_room_payload(*, room_slug: str, payload: dict) -> list[dict]:
    """
    Backwards-compatible functional wrapper for ChatConsumer.
    """
    return AgentMlsBridge.handle_room_payload(
        room_slug=room_slug,
        payload=payload,
    )