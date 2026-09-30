"""The ACE editor's other doors (2026-09-29): who may save or delete, what an
encrypted file or a host with edits off answers, what a history that cannot
be written costs the save (nothing), and the sync socket end to end — who it
admits, what it writes, what it tells the others and the lock it respects.

`toto` is a namespace package: run as `manage.py test toto.editor.tests_more_doors`."""

import hashlib
import json
from unittest import mock

from asgiref.sync import async_to_sync
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from diff_match_patch import diff_match_patch
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings
from django.urls import reverse

from toto.editor import views
from toto.editor.routing import websocket_urlpatterns
from toto.editor.tests import EditorTestCase
from toto.vault import locks
from toto.vault.models import VaultFile

IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


class SaveDoorTests(EditorTestCase):
    def save(self, vault_file=None, **payload):
        return self.client.post(reverse("editor:text_save", args=[(vault_file or self.file).pk]),
                                payload)

    @override_settings(VAULT_FILE_EDITS=False)
    def test_a_host_with_edits_off_refuses_every_save(self):
        response = self.save(content="changed\n")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._on_disk(), "first\n")

    def test_nobody_signed_in_is_told_so_not_crashed(self):
        request = RequestFactory().post("/editor/text/1/save/", {"content": "x"})
        request.user = AnonymousUser()
        response = views.save_file(request, self.file.pk)
        self.assertEqual(response.status_code, 401)

    def test_a_colleague_cannot_save_the_owner_s_file(self):
        self.client.force_login(self.other)
        self.assertEqual(self.save(content="mine now\n").status_code, 404)
        self.assertEqual(self._on_disk(), "first\n")

    def test_an_encrypted_file_is_not_written_through(self):
        VaultFile.objects.filter(pk=self.file.pk).update(is_encrypted=True)
        response = self.save(content="plaintext\n")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Decrypt it first", response.json()["error"])

    def test_the_size_follows_the_bytes_written(self):
        body = "zażółć\n"
        self.save(content=body)
        self.file.refresh_from_db()
        self.assertEqual(self.file.file_size_bytes, len(body.encode("utf-8")))

    def test_a_history_that_cannot_be_written_does_not_fail_the_save(self):
        with mock.patch("toto.vault.versions.save_version", side_effect=OSError("disk full")), \
                self.assertLogs("toto.editor", "ERROR"):
            response = self.save(content="second\n")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("version", body)
        self.assertEqual(body["content_hash"], hashlib.sha256(b"second\n").hexdigest())
        self.assertEqual(self._on_disk(), "second\n")


class DeleteDoorTests(EditorTestCase):
    def delete(self, vault_file=None):
        return self.client.post(reverse("editor:text_delete", args=[(vault_file or self.file).pk]))

    def test_the_owner_moves_it_to_the_trash_bytes_kept(self):
        # The vault's trash (2026-10-01): hidden everywhere, restorable.
        storage, name = self.file.file.storage, self.file.file.name
        response = self.delete()
        self.assertEqual(response.json(), {"status": "ok", "redirect": "/vault/"})
        self.assertFalse(VaultFile.objects.filter(pk=self.file.pk).exists())
        self.assertIsNotNone(VaultFile.all_objects.get(pk=self.file.pk).trashed_at)
        self.assertTrue(storage.exists(name))

    def test_a_colleague_cannot_delete_the_owner_s_file(self):
        self.client.force_login(self.other)
        self.assertEqual(self.delete().status_code, 404)
        self.assertTrue(VaultFile.objects.filter(pk=self.file.pk).exists())

    def test_a_get_never_deletes(self):
        response = self.client.get(reverse("editor:text_delete", args=[self.file.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(VaultFile.objects.filter(pk=self.file.pk).exists())

    def test_nobody_signed_in_is_told_so(self):
        request = RequestFactory().post("/editor/text/1/delete/")
        request.user = AnonymousUser()
        self.assertEqual(views.delete_file(request, self.file.pk).status_code, 401)
        self.assertTrue(VaultFile.objects.filter(pk=self.file.pk).exists())


class PageTests(EditorTestCase):
    def test_a_colleague_cannot_open_the_owner_s_file(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("editor:text_display", args=[self.file.pk]))
        self.assertEqual(response.status_code, 404)

    def test_an_encrypted_file_opens_to_the_lock_page(self):
        sealed = self._make("sealed-body-text\n", title="sealed.txt")
        VaultFile.objects.filter(pk=sealed.pk).update(is_encrypted=True)
        response = self.client.get(reverse("editor:text_display", args=[sealed.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "sealed-body-text", status_code=403)

    def test_bytes_that_are_not_text_open_as_a_notice(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        binary = VaultFile.objects.create(owner=self.owner, title="blob.txt", file_type="text",
                                          bucket=self.bucket,
                                          file=SimpleUploadedFile("blob.txt", b"\xff\xfe\x00"))
        response = self.client.get(reverse("editor:text_display", args=[binary.pk]))
        self.assertContains(response, "[Unable to read file content]")


class SurfaceTests(EditorTestCase):
    def test_each_file_type_gets_its_own_assistant_surface(self):
        view = views.TextFileDisplayView()
        for file_type, surface in (("python", "editor-code"), ("html", "editor-markup"),
                                   ("latex", "editor-latex"), ("text", "editor-text"),
                                   ("pdf", "editor-text")):
            with self.subTest(file_type=file_type):
                self.assertEqual(view.resolve_steven_surface(VaultFile(file_type=file_type)),
                                 surface)

    def test_a_drawing_is_never_offered_a_prose_assistant(self):
        self.assertEqual(views.SvgFileDisplayView().resolve_steven_surface(
            VaultFile(file_type="html")), "")

    def test_an_html_file_without_the_renderer_has_no_pdf_button(self):
        with mock.patch("django.apps.apps.is_installed", return_value=False):
            self.assertEqual(views.HtmlFileDisplayView().get_extra_context(self.file),
                             {"pdf_url": ""})

    def test_a_latex_file_without_the_compiler_has_no_compile_button(self):
        with mock.patch("django.apps.apps.is_installed", return_value=False):
            self.assertEqual(views.LatexFileDisplayView().get_extra_context(self.file), {})


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class SyncSocketDoorTests(EditorTestCase):
    """Each conversation runs in ONE event loop: a communicator's application
    task dies with the loop that started it."""

    def socket(self, user):
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns), f"/ws/editor/file/{self.file.pk}/")
        communicator.scope["user"] = user
        return communicator

    def admitted(self, user):
        async def talk():
            communicator = self.socket(user)
            connected, _ = await communicator.connect(timeout=5)
            await communicator.disconnect()
            return connected

        return async_to_sync(talk)()

    def exchange(self, message, *, listeners=0):
        """The owner sends one message; returns (their answer, what each listener heard)."""

        async def talk():
            writer = self.socket(self.owner)
            others = [self.socket(self.owner) for _ in range(listeners)]
            for communicator in [writer, *others]:
                connected, _ = await communicator.connect(timeout=5)
                self.assertTrue(connected)
            await writer.send_to(text_data=json.dumps(message))
            answer = json.loads(await writer.receive_from(timeout=5))
            heard = [json.loads(await other.receive_from(timeout=5)) for other in others]
            for communicator in [writer, *others]:
                await communicator.disconnect()
            return answer, heard

        return async_to_sync(talk)()

    def test_nobody_signed_in_is_turned_away(self):
        self.assertFalse(self.admitted(AnonymousUser()))

    def test_a_colleague_is_turned_away_from_the_owner_s_file(self):
        self.assertFalse(self.admitted(self.other))

    def test_the_owner_is_admitted(self):
        self.assertTrue(self.admitted(self.owner))

    def test_an_encrypted_file_admits_nobody(self):
        VaultFile.objects.filter(pk=self.file.pk).update(is_encrypted=True)
        self.assertFalse(self.admitted(self.owner))

    @override_settings(VAULT_FILE_EDITS=False)
    def test_a_host_with_edits_off_admits_nobody(self):
        self.assertFalse(self.admitted(self.owner))

    def test_the_owner_s_full_text_is_written_and_its_hash_handed_back(self):
        answer, _ = self.exchange({"type": "full", "content": "rewritten\n"})
        self.assertEqual(answer, {"type": "hash",
                                  "content_hash": hashlib.sha256(b"rewritten\n").hexdigest()})
        self.assertEqual(self._on_disk(), "rewritten\n")

    def test_a_patch_is_applied_to_what_is_on_disk(self):
        dmp = diff_match_patch()
        patch = dmp.patch_toText(dmp.patch_make("first\n", "first and more\n"))
        self.exchange({"type": "patch", "patch": patch})
        self.assertEqual(self._on_disk(), "first and more\n")

    def test_somebody_else_s_lock_refuses_the_write(self):
        locks.acquire(self.file, self.other)
        answer, _ = self.exchange({"content": "overwrite\n"})
        self.assertEqual(answer, {"type": "locked", "locked_by": "colleague"})
        self.assertEqual(self._on_disk(), "first\n")

    def test_the_other_sessions_receive_the_new_text(self):
        answer, (heard,) = self.exchange({"type": "full", "content": "shared\n"}, listeners=1)
        digest = hashlib.sha256(b"shared\n").hexdigest()
        self.assertEqual(answer["type"], "hash")
        self.assertEqual(heard, {"type": "full", "content": "shared\n", "patch": "",
                                 "content_hash": digest})
