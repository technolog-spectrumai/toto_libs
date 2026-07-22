"""
Manta test suite — command classes, the FileJob model, and the form-based
builder (ffmpeg/ffprobe + transcribe commands).
"""

import os
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, Client as DjangoClient, SimpleTestCase, override_settings
from django.urls import reverse

from .commands import OPERATIONS, get_command
from .factory import FFmpegCommandFactory, COMMAND_FILE_PRESETS, UnknownOperation
from .models import FileJob, MediaJob


# ---------------------------------------------------------------------------
# Command classes / registry
# ---------------------------------------------------------------------------

class CommandRegistryTests(SimpleTestCase):
    def test_all_commands_registered(self):
        self.assertEqual(len(OPERATIONS), 17)
        for key in OPERATIONS:
            cmd = get_command(key)
            self.assertTrue(cmd.label)
            self.assertTrue(cmd.inputs, f"{key} has no inputs")
            self.assertTrue(cmd.outputs, f"{key} has no outputs")

    def test_unknown_command_raises(self):
        with self.assertRaises(UnknownOperation):
            get_command("nope")

    def test_ffmpeg_build_spec(self):
        spec = FFmpegCommandFactory().build(
            "compress", input_name="clip.mp4", params={"quality": "high", "output_name": "out"})
        self.assertIn("-i clip.mp4", spec.shell_display)
        self.assertEqual(spec.output_names, ("out.mp4",))

    def test_probe_is_ffprobe_backend(self):
        cmd = get_command("probe")
        self.assertEqual(cmd.backend, "ffprobe")
        spec = cmd().build_spec(input_name="clip.mp4")
        self.assertEqual(spec.output_names, ("clip.ffprobe.json",))

    def test_gif_two_passes(self):
        spec = get_command("gif")().build_spec(input_name="clip.mp4", params={"output_name": "anim"})
        self.assertEqual(len(spec.commands), 2)
        self.assertEqual(spec.output_names, ("anim.gif",))

    def test_secondary_input_used(self):
        spec = get_command("replace_audio")().build_spec(
            input_name="clip.mp4", extra_input_names=["track.mp3"], params={"output_name": "dub"})
        self.assertIn("-i clip.mp4", spec.shell_display)
        self.assertIn("-i track.mp3", spec.shell_display)

    def test_service_commands(self):
        tr = get_command("transcribe")
        self.assertEqual((tr.backend, tr.backend_label, tr.service_key), ("service", "whisper", "transcription"))
        self.assertIn("whisper", tr().describe(input_name="a.mp3", params={"language": "en"}))

    def test_preset_metadata(self):
        self.assertEqual(set(COMMAND_FILE_PRESETS), set(OPERATIONS))
        self.assertEqual(get_command("replace_audio").inputs["audio"]["file_type"], "audio")
        self.assertEqual(get_command("add_subtitles").inputs["subtitles"]["file_type"], "subtitle")
        self.assertEqual(get_command("add_watermark").inputs["watermark"]["file_type"], "image")
        self.assertEqual(get_command("extract_mp3").outputs["output"]["extension"], "mp3")
        self.assertEqual(get_command("thumbnail").outputs["output"]["extension"], "jpg")
        self.assertTrue(next(iter(get_command("concat").inputs.values()))["multiple"])

    def test_command_tabs(self):
        from .commands import TAB_ORDER, commands_for_tab

        self.assertEqual(get_command("compress").tab, "ffmpeg")
        self.assertEqual(get_command("probe").tab, "ffprobe")
        self.assertEqual(get_command("transcribe").tab, "transcribe")
        # The focused tabs hold exactly one command; ffmpeg holds the rest.
        self.assertEqual([c.key for c in commands_for_tab("ffprobe")], ["probe"])
        self.assertEqual([c.key for c in commands_for_tab("transcribe")], ["transcribe"])
        self.assertGreater(len(commands_for_tab("ffmpeg")), 1)
        # Every registered command belongs to a known tab.
        self.assertTrue(all(get_command(k).tab in TAB_ORDER for k in OPERATIONS))


# ---------------------------------------------------------------------------
# FileJob model
# ---------------------------------------------------------------------------

class FileJobModelTests(TestCase):
    def test_mediajob_proxy(self):
        u = User.objects.create_user("m", password="x")
        job = MediaJob.objects.create(command="compress", owner=u, inputs=[7, 8], params={})
        self.assertEqual(job.primary_input, 7)
        self.assertEqual(job.extra_inputs, [8])
        self.assertTrue(MediaJob._meta.proxy)
        self.assertTrue(FileJob.objects.filter(pk=job.pk).exists())


# ---------------------------------------------------------------------------
# Builder view + execution
# ---------------------------------------------------------------------------

@override_settings(MANTA_WORK_ROOT="/tmp/manta_test", MEDIA_ROOT="/tmp/media_manta")
class BuilderTests(TestCase):
    URL = "/manta/"

    def setUp(self):
        self.curator = User.objects.create_user("cur", password="pass")
        self.owner = User.objects.create_user("own", password="pass")
        self.stranger = User.objects.create_user("str", password="pass")
        self.client = DjangoClient()
        self.client.login(username="own", password="pass")
        p = patch("toto.ui.page.PageProcessor._get_config", return_value=None)
        p.start(); self.addCleanup(p.stop)

        from toto.vault.models import Bucket
        self.bucket = Bucket.objects.create(name="B", owner=self.curator, slug="mb")
        self.src = self._vf("source.mp4", "video")
        self.extra = self._vf("second.mp4", "video")
        self.audio = self._vf("song.mp3", "audio")
        self.image = self._vf("scan.png", "image")
        self.secret = self._vf("secret.mp4", "video", owner=self.stranger)

    def _vf(self, name, ftype, *, owner=None):
        from toto.vault.models import VaultFile
        os.makedirs("/tmp/media_manta/vault/files", exist_ok=True)
        vf = VaultFile(owner=owner or self.owner, title=name, file_type=ftype, bucket=self.bucket)
        vf.file.save(name, ContentFile(b"x"), save=True)
        return vf

    def _ajax(self, data):
        return self.client.post(self.URL, data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    # -- source / pickers -------------------------------------------------

    def test_no_source_shows_all_media(self):
        # Landing shows every accessible media file (not just video).
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "source.mp4")   # video
        self.assertContains(resp, "song.mp3")     # audio
        self.assertContains(resp, "scan.png")     # image

    def test_picking_audio_defaults_to_transcribe(self):
        resp = self.client.get(self.URL + f"?file={self.audio.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "whisper")      # audio → transcribe command

    def test_compress_shows_form_and_backend(self):
        resp = self.client.get(self.URL + f"?file={self.src.pk}&op=compress")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ffmpeg")          # backend chip
        self.assertContains(resp, "Options")

    def test_concat_shows_extra_video_picker(self):
        resp = self.client.get(self.URL + f"?file={self.src.pk}&op=concat")
        self.assertContains(resp, "second.mp4")
        self.assertContains(resp, "Videos to concatenate")

    def test_transcribe_source_is_audio(self):
        resp = self.client.get(self.URL + f"?file={self.audio.pk}&op=transcribe")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "whisper")          # backend chip
        self.assertContains(resp, "song.mp3")

    def test_wrong_source_type_rejected(self):
        resp = self.client.get(self.URL + f"?file={self.audio.pk}&op=compress")
        self.assertContains(resp, "needs a video file")

    # -- tabs -------------------------------------------------------------

    def test_tab_bar_lists_all_families(self):
        resp = self.client.get(self.URL)
        for label in ("ffmpeg", "ffprobe", "Transcribe"):
            self.assertContains(resp, label)

    def test_ffmpeg_dropdown_excludes_service_commands(self):
        # The command dropdown is ffmpeg-only; probe/transcribe have own tabs.
        resp = self.client.get(self.URL + f"?file={self.src.pk}&op=compress")
        self.assertNotContains(resp, "Transcribe (speech → text)")

    def test_ffprobe_tab_accepts_any_media(self):
        resp = self.client.get(self.URL + f"?tab=ffprobe&file={self.audio.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ffprobe")      # backend chip
        self.assertContains(resp, "song.mp3")     # audio allowed, not just video

    def test_transcribe_language_is_dropdown(self):
        resp = self.client.get(self.URL + f"?tab=transcribe&file={self.audio.pk}")
        self.assertContains(resp, "<select")      # friendly language picker
        self.assertContains(resp, "Auto-detect")

    # -- upload (reuses the vault upload API) ------------------------------

    def test_upload_panel_shown_on_focused_tabs(self):
        resp = self.client.get(self.URL + "?tab=transcribe")
        self.assertContains(resp, "Upload a new file")
        self.assertContains(resp, "Personal bucket")          # default destination
        self.assertContains(resp, "/vault/api/files/upload/")  # reuses vault API

    def test_upload_panel_lists_user_buckets(self):
        from toto.vault.models import Bucket
        Bucket.objects.create(name="My Own Bucket", owner=self.owner, slug="my-own")
        resp = self.client.get(self.URL + "?tab=transcribe")
        self.assertContains(resp, "My Own Bucket")
        # Buckets the user does not own (curator's, slug "mb") are not offered.
        self.assertNotContains(resp, 'value="mb"')

    def test_no_upload_panel_on_ffmpeg_tab(self):
        resp = self.client.get(self.URL + f"?file={self.src.pk}&op=compress")
        self.assertNotContains(resp, "Upload a new file")

    # -- quick record & transcribe ----------------------------------------

    QUICK_URL = "/manta/quick-transcribe/"

    def test_quick_modal_only_on_transcribe_tab(self):
        on = self.client.get(self.URL + "?tab=transcribe")
        self.assertContains(on, "Quick record")
        self.assertContains(on, "/manta/quick-transcribe/")
        off = self.client.get(self.URL + "?tab=ffprobe")
        self.assertNotContains(off, "Quick record")

    def test_quick_transcribe_creates_text_file(self):
        with patch("toto.transcription.services.transcribe_demo_file",
                   return_value={"text": "hello world", "segments": []}) as tr:
            resp = self.client.post(self.QUICK_URL, {
                "audio_id": self.audio.pk, "output_name": "transcript.txt", "language": "en"})
        self.assertTrue(tr.called)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["text"], "hello world")

        from toto.vault.models import VaultFile
        out = VaultFile.objects.get(pk=data["output"]["id"])
        self.assertEqual(out.file_type, "text")
        self.assertEqual(out.bucket_id, self.audio.bucket_id)   # same bucket as audio
        self.assertEqual(out.owner, self.owner)
        self.assertEqual(out.file.read(), b"hello world")

    def test_quick_transcribe_default_name_and_uniqueness(self):
        with patch("toto.transcription.services.transcribe_demo_file",
                   return_value={"text": "a", "segments": []}):
            first = self.client.post(self.QUICK_URL, {"audio_id": self.audio.pk}).json()
            second = self.client.post(self.QUICK_URL, {"audio_id": self.audio.pk}).json()
        self.assertEqual(first["output"]["title"], "transcript.txt")
        self.assertEqual(second["output"]["title"], "transcript-1.txt")  # no overwrite

    def test_quick_transcribe_rejects_non_audio(self):
        resp = self.client.post(self.QUICK_URL, {"audio_id": self.image.pk})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    def test_quick_transcribe_requires_access(self):
        resp = self.client.post(self.QUICK_URL, {"audio_id": self.secret.pk})
        self.assertEqual(resp.status_code, 404)

    def test_quick_transcribe_get_not_allowed(self):
        self.assertEqual(self.client.get(self.QUICK_URL).status_code, 405)

    # -- job page: wait + spinner while running ---------------------------

    def test_job_status_endpoint(self):
        job = FileJob.objects.create(command="transcribe", owner=self.owner, status=FileJob.Status.PENDING)
        data = self.client.get(f"/manta/jobs/{job.pk}/status/").json()
        self.assertEqual(data["status"], "pending")
        self.assertFalse(data["is_terminal"])
        job.status = FileJob.Status.DONE
        job.save(update_fields=["status"])
        data = self.client.get(f"/manta/jobs/{job.pk}/status/").json()
        self.assertTrue(data["is_terminal"])

    def test_job_detail_spinner_while_running(self):
        job = FileJob.objects.create(command="transcribe", owner=self.owner, status=FileJob.Status.PENDING)
        resp = self.client.get(f"/manta/jobs/{job.pk}/")
        self.assertContains(resp, "Processing")
        self.assertContains(resp, "fa-spinner")
        self.assertContains(resp, f"/manta/jobs/{job.pk}/status/")   # poll target
        self.assertNotContains(resp, "Result")                       # no result box yet

    def test_job_detail_shows_result_when_done(self):
        job = FileJob.objects.create(command="transcribe", owner=self.owner,
                                     status=FileJob.Status.DONE, output={"files": []})
        resp = self.client.get(f"/manta/jobs/{job.pk}/")
        self.assertContains(resp, "Result")
        self.assertNotContains(resp, "Processing")

    # -- live preview -----------------------------------------------------

    def test_preview_ffmpeg(self):
        data = self._ajax({"file": self.src.pk, "op": "compress", "action": "preview",
                           "quality": "medium", "output_name": "compressed"}).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["backend"], "ffmpeg")
        self.assertIn("compressed.mp4", data["command"])

    def test_preview_service(self):
        data = self._ajax({"file": self.audio.pk, "op": "transcribe", "action": "preview",
                           "language": "en"}).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["backend"], "whisper")
        self.assertIn("whisper", data["command"])

    # -- run --------------------------------------------------------------

    def test_run_ffmpeg_creates_job(self):
        with patch("toto.manta.tasks_direct.run_direct_job.delay") as delay:
            resp = self.client.post(self.URL, {"file": self.src.pk, "op": "compress", "action": "run",
                                               "quality": "medium", "output_name": "out"})
        job = FileJob.objects.filter(command="compress").last()
        self.assertEqual(job.inputs, [self.src.pk])
        self.assertTrue(delay.called)
        self.assertRedirects(resp, reverse("manta:job_detail", args=[job.pk]), fetch_redirect_response=False)

    def test_run_concat_with_extra(self):
        with patch("toto.manta.tasks_direct.run_direct_job.delay"):
            self.client.post(self.URL, {"file": self.src.pk, "op": "concat", "action": "run",
                                        "output_name": "merged", "videos": [self.extra.pk]})
        job = FileJob.objects.filter(command="concat").last()
        self.assertEqual(job.inputs, [self.src.pk, self.extra.pk])

    def test_run_transcribe_creates_job(self):
        with patch("toto.manta.tasks_direct.run_direct_job.delay") as delay:
            self.client.post(self.URL, {"file": self.audio.pk, "op": "transcribe", "action": "run",
                                        "language": "en"})
        job = FileJob.objects.filter(command="transcribe").last()
        self.assertEqual(job.inputs, [self.audio.pk])
        self.assertTrue(delay.called)

    def test_unauthorized_extra_rejected(self):
        before = FileJob.objects.count()
        with patch("toto.manta.tasks_direct.run_direct_job.delay") as delay:
            resp = self.client.post(self.URL, {"file": self.src.pk, "op": "concat", "action": "run",
                                               "output_name": "merged", "videos": [self.secret.pk]})
        self.assertEqual(FileJob.objects.count(), before)
        self.assertFalse(delay.called)
        self.assertContains(resp, "access")

    # -- execution (command family owns it) -------------------------------

    @patch("toto.manta.commands.backends.subprocess.run")
    def test_ffmpeg_command_executes(self, mock_run):
        def fake_run(argv, cwd=None, **kw):
            with open(os.path.join(cwd, argv[-1]), "w") as fh:
                fh.write("x")
            return MagicMock(returncode=0, stdout="", stderr="")
        mock_run.side_effect = fake_run

        job = FileJob.objects.create(command="compress", owner=self.owner,
                                     inputs=[self.src.pk], params={"output_name": "out", "quality": "medium"})
        get_command("compress")().execute(job)
        job.refresh_from_db()
        self.assertEqual(job.status, FileJob.Status.DONE)
        self.assertIn("ffmpeg", job.output["command"])
        self.assertEqual(len(job.output["files"]), 1)

    def test_service_command_dispatches_file_service_run(self):
        from toto.fileservices.models import FileServiceRun
        with patch("toto.fileservices.dispatch.dispatch_run") as disp:
            job = FileJob.objects.create(command="transcribe", owner=self.owner,
                                         inputs=[self.audio.pk], params={"language": "en"})
            get_command("transcribe")().execute(job)
        job.refresh_from_db()
        run = FileServiceRun.objects.latest("started_at")
        self.assertEqual(run.service_key, "transcription")
        self.assertEqual(job.output["service_run_id"], run.id)
        self.assertEqual(job.status, FileJob.Status.RUNNING)
        self.assertTrue(disp.called)
