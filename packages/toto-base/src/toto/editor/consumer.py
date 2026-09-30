import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from diff_match_patch import diff_match_patch


class BaseFileSyncConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer that syncs a VaultFile's content across sessions.
    Subclasses must set room_prefix to a unique string (e.g. "editor_file").
    """
    room_prefix: str = "editor_file"

    async def connect(self):
        self.file_pk = self.scope["url_route"]["kwargs"]["file_pk"]
        # The socket rewrites file content, so it carries the same contract as
        # editor save_file: an authenticated OWNER of an unencrypted file, on a
        # host that allows edits at all, cleared for the file's bucket if it
        # is kept. Anyone else is refused at the door.
        user = self.scope.get("user")
        self.user = user if user is not None and user.is_authenticated else None
        if self.user is None or not await self._may_edit():
            await self.close()
            return
        self.room = f"{self.room_prefix}_{self.file_pk}"
        self.dmp = diff_match_patch()
        await self.channel_layer.group_add(self.room, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if getattr(self, "room", None):
            await self.channel_layer.group_discard(self.room, self.channel_name)

    def _own_files(self):
        """This user's files, less those their bucket's clearances hide.

        The socket is the editor's save door by another road, so it asks what
        `views._own_file` asks (2026-09-30): an owner who lacks their bucket's
        clearance is turned away at `connect`, and one whose clearance goes
        while the socket is open is closed on the next message.
        """
        from toto.vault import access
        from toto.vault.models import VaultFile
        return access.gate_by_bucket(
            self.user, VaultFile.objects.filter(pk=self.file_pk, owner=self.user))

    @database_sync_to_async
    def _may_edit(self) -> bool:
        from toto.vault.models import file_edits_allowed
        if not file_edits_allowed():
            return False
        return self._own_files().filter(is_encrypted=False).exists()

    @database_sync_to_async
    def read_file(self) -> str:
        vf = self._own_files().get()
        with vf.file.open("r") as f:
            return f.read()

    @database_sync_to_async
    def _lock_holder(self) -> str:
        """Whoever else is holding the edit lock, or "".

        The socket is the writer this app most easily forgets: it rewrites the
        file on every buffer change, long before anybody presses Save, so a lock
        checked only in `save_file` protects nothing here. `connect` already
        refuses a non-owner, but the OWNER is not automatically the holder — the
        vault lends a file out through a shared directory and through cyprian's
        access plugin, so a collaborator can be mid-edit in a file its owner
        also has open.

        `may_write` answers the useful form of the question: only somebody
        else's LIVE lock refuses, so the owner's own second tab is not locked
        out of a file by itself.
        """
        from toto.vault import locks
        from toto.vault.models import VaultFile

        vault_file = VaultFile.objects.filter(pk=self.file_pk).first()
        if vault_file is None or locks.may_write(vault_file, self.user):
            return ""
        held = locks.holder_of(vault_file)
        return held.holder.get_username() if held else ""

    @database_sync_to_async
    def write_file(self, content: str):
        """Screen, then write. Returns ``(verdict, content_hash)``.

        Writes nothing if the screen refuses, and the hash comes back so the
        sender can carry it as the `base_hash` for its next save. Without that
        the browser had to hash the buffer itself, because this socket moves
        `content_hash` on every keystroke and a base remembered from page load
        would 409 on the first save after one.

        This is the door most easily missed, and the one that most needs the
        check: on the patch path the sending client never sees the final bytes,
        so a patch that is innocent in isolation can still compose into hostile
        content. Screening the *result* is the only place that catches it.
        """
        import hashlib

        from toto.vault import scanning

        vf = self._own_files().get()
        if scanning.should_scan(self.user, vf.file_type, door="socket"):
            verdict = scanning.scan(content, file_type=vf.file_type,
                                    filename=vf.title)
            if not verdict.ok:
                scanning.record(vf, verdict, user=self.user, door="socket")
                return verdict, ""
        else:
            verdict = scanning.Verdict.clean(scanned=False)

        with vf.file.open("w") as f:
            f.write(content)
        encoded = content.encode("utf-8")
        vf.content_hash = hashlib.sha256(encoded).hexdigest()
        vf.file_size_bytes = len(encoded)
        vf.save(update_fields=["content_hash", "file_size_bytes"])
        if verdict.scanned:
            scanning.record(vf, verdict, user=self.user, door="socket")
        return verdict, vf.content_hash

    async def receive(self, text_data):
        data = json.loads(text_data)
        incoming_content = data.get("content", "")
        incoming_patch = data.get("patch", "")
        msg_type = data.get("type", "full")

        # The door again, not only at `connect`: a clearance taken away, an
        # encryption or edits switched off since then close the socket rather
        # than let it keep writing (2026-09-30).
        if not await self._may_edit():
            await self.close()
            return

        # Before anything is read or written: the lock is the whole point of
        # this check being here rather than only in save_file.
        holder = await self._lock_holder()
        if holder:
            await self.send(text_data=json.dumps({
                "type": "locked",
                "locked_by": holder,
            }))
            return

        current_content = await self.read_file()

        if incoming_patch:
            patches = self.dmp.patch_fromText(incoming_patch)
            new_content, _ = self.dmp.patch_apply(patches, current_content)
        else:
            new_content = incoming_content

        verdict, content_hash = await self.write_file(new_content)
        if not verdict.ok:
            # Tell the sender, and nobody else: the other sessions still hold
            # the last good content, and broadcasting a refusal would only
            # invite them to overwrite it with what they have.
            await self.send(text_data=json.dumps({
                "type": "refused",
                "reason": verdict.reason,
                "detail": verdict.detail,
                "line": verdict.line,
            }))
            return

        await self.channel_layer.group_send(
            self.room,
            {
                "type": "sync_message",
                "sender": self.channel_name,
                "msg_type": msg_type,
                "content": new_content,
                "patch": incoming_patch,
                "content_hash": content_hash,
            },
        )

    async def sync_message(self, event):
        if event["sender"] == self.channel_name:
            # The sender still needs the hash it just caused: it is the
            # precondition its next save will carry.
            await self.send(text_data=json.dumps({
                "type": "hash",
                "content_hash": event.get("content_hash", ""),
            }))
            return
        await self.send(text_data=json.dumps({
            "type": event["msg_type"],
            "content": event["content"],
            "patch": event["patch"],
            "content_hash": event.get("content_hash", ""),
        }))


class EditorFileSyncConsumer(BaseFileSyncConsumer):
    room_prefix = "editor_file"
