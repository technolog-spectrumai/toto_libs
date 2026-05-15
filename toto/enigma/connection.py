import json
import uuid
import redis
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from enigma_py import EnigmaEngine

from toto.enigma.models import State, Participant

# Redis client
redis_client = redis.Redis.from_url(settings.REDIS_URL)

class RedisEnigmaConnection:
    REDIS_MSG_PREFIX = "enigma:processed:"
    REDIS_KEY_PREFIX = "enigma:keypackage:"

    def __init__(self, *, state_id):
        self.state_id = state_id

    @classmethod
    def for_participant(cls, participant: Participant):
        if not participant.agent_id:
            raise ValueError("Only agent participants can have an EnigmaConnection.")
        state = State.objects.get(participant=participant)
        return cls(state_id=state.id)

    @property
    def state(self) -> State:
        return State.objects.select_related("participant", "participant__room").get(id=self.state_id)

    @property
    def participant(self) -> Participant:
        return self.state.participant

    @property
    def room(self):
        return self.participant.room

    def _locked_state(self) -> State:
        return State.objects.select_for_update().select_related("participant").get(id=self.state_id)

    @staticmethod
    def _ensure_active(state: State):
        if state.status != State.Status.ACTIVE:
            raise RuntimeError(f"State is not active; status={state.status!r}")

    @staticmethod
    def _mark_error(state: State, exc: Exception):
        state.status = State.Status.ERROR
        state.last_error = str(exc)
        state.save(update_fields=["status", "last_error", "updated_at"])

    # --- Processed Messages in Redis ---
    def _redis_message_key(self, message_id: str) -> str:
        return f"{self.REDIS_MSG_PREFIX}{self.state_id}:{message_id}"

    def receive(self, *, message_id: str, ciphertext: bytes):
        redis_key = self._redis_message_key(message_id)
        try:
            # Atomic NX set to guarantee idempotency
            added = redis_client.set(redis_key, 1, nx=True, ex=24*3600)
        except redis.RedisError:
            raise RuntimeError("Unable to verify message idempotency; please reconnect.")

        if not added:
            return None  # Already processed

        state = self._locked_state()
        self._ensure_active(state)

        raw_state = bytes(state.encrypted_state)
        try:
            new_state, plaintext = EnigmaEngine.process_message_from_state(raw_state, ciphertext)
        except Exception as exc:
            self._mark_error(state, exc)
            raise

        state.encrypted_state = bytes(new_state)
        state.state_version += 1
        state.last_error = ""
        state.save(update_fields=["encrypted_state", "state_version", "last_error", "updated_at"])

        if plaintext is None:
            return None

        return {"plaintext": bytes(plaintext), "message_id": message_id}

    # --- KeyPackage in Redis ---
    def create_key_package(self, *, ttl_seconds=3600):
        state = self._locked_state()
        self._ensure_active(state)

        raw_state = bytes(state.encrypted_state)
        try:
            new_state, public_key_package = EnigmaEngine.key_package_from_state(raw_state)
        except Exception as exc:
            self._mark_error(state, exc)
            raise

        # Update state
        state.encrypted_state = bytes(new_state)
        state.state_version += 1
        state.status = State.Status.PENDING_WELCOME
        state.last_error = ""
        state.save(update_fields=["encrypted_state", "state_version", "status", "last_error", "updated_at"])

        key_id = str(uuid.uuid4())
        redis_key = f"{self.REDIS_KEY_PREFIX}{self.state_id}:{key_id}"
        data = {
            "public_key": public_key_package.hex(),
            "used": False,
            "expires_at": int(timezone.now().timestamp() + ttl_seconds)
        }
        try:
            redis_client.set(redis_key, json.dumps(data), ex=ttl_seconds)
        except redis.RedisError:
            raise RuntimeError("Unable to store key package; please reconnect.")

        return key_id, public_key_package

    def use_key_package(self, key_id: str):
        redis_key = f"{self.REDIS_KEY_PREFIX}{self.state_id}:{key_id}"
        try:
            raw = redis_client.get(redis_key)
            if not raw:
                raise RuntimeError("KeyPackage missing; reconnect required")
            data = json.loads(raw)
            if data.get("used"):
                return False  # already used
            data["used"] = True
            ttl = max(data.get("expires_at", 0) - int(timezone.now().timestamp()), 1)
            redis_client.set(redis_key, json.dumps(data), ex=ttl)
            return True
        except redis.RedisError:
            raise RuntimeError("Unable to verify KeyPackage; reconnect required")