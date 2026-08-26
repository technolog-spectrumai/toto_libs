"""
Manta test suite — command classes, the FileJob model, and the form-based
builder (ffmpeg/ffprobe commands).
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
        self.assertEqual(len(OPERATIONS), 16)
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
        # The focused tab holds exactly one command; ffmpeg holds the rest.
        self.assertEqual([c.key for c in commands_for_tab("ffprobe")], ["probe"])
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

    def test_picking_audio_lands_on_ffmpeg(self):
        # Audio used to default to the transcribe tab; that app is parked, and
        # ffmpeg handles audio (extract, replace, remove) perfectly well.
        resp = self.client.get(self.URL + f"?file={self.audio.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "song.mp3")

    def test_compress_shows_form_and_backend(self):
        resp = self.client.get(self.URL + f"?file={self.src.pk}&op=compress")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ffmpeg")          # backend chip
        self.assertContains(resp, "Options")

    def test_concat_shows_extra_video_picker(self):
        resp = self.client.get(self.URL + f"?file={self.src.pk}&op=concat")
        self.assertContains(resp, "second.mp4")
        self.assertContains(resp, "Videos to concatenate")

    def test_wrong_source_type_rejected(self):
        resp = self.client.get(self.URL + f"?file={self.audio.pk}&op=compress")
        self.assertContains(resp, "needs a video file")

    # -- tabs -------------------------------------------------------------

    def test_tab_bar_lists_all_families(self):
        resp = self.client.get(self.URL)
        for label in ("ffmpeg", "ffprobe"):
            self.assertContains(resp, label)

    def test_ffprobe_tab_accepts_any_media(self):
        resp = self.client.get(self.URL + f"?tab=ffprobe&file={self.audio.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ffprobe")      # backend chip
        self.assertContains(resp, "song.mp3")     # audio allowed, not just video

    def test_upload_panel_shown_on_focused_tabs(self):
        resp = self.client.get(self.URL + "?tab=ffprobe")
        self.assertContains(resp, "Upload a new file")
        self.assertContains(resp, "Personal bucket")          # default destination
        self.assertContains(resp, "/vault/api/files/upload/")  # reuses vault API

    def test_upload_panel_lists_user_buckets(self):
        from toto.vault.models import Bucket
        Bucket.objects.create(name="My Own Bucket", owner=self.owner, slug="my-own")
        resp = self.client.get(self.URL + "?tab=ffprobe")
        self.assertContains(resp, "My Own Bucket")
        # Buckets the user does not own (curator's, slug "mb") are not offered.
        self.assertNotContains(resp, 'value="mb"')

    def test_no_upload_panel_on_ffmpeg_tab(self):
        resp = self.client.get(self.URL + f"?file={self.src.pk}&op=compress")
        self.assertNotContains(resp, "Upload a new file")

    def test_job_status_endpoint(self):
        job = FileJob.objects.create(command="compress", owner=self.owner, status=FileJob.Status.PENDING)
        data = self.client.get(f"/manta/jobs/{job.pk}/status/").json()
        self.assertEqual(data["status"], "pending")
        self.assertFalse(data["is_terminal"])
        job.status = FileJob.Status.DONE
        job.save(update_fields=["status"])
        data = self.client.get(f"/manta/jobs/{job.pk}/status/").json()
        self.assertTrue(data["is_terminal"])

    def test_job_detail_spinner_while_running(self):
        job = FileJob.objects.create(command="compress", owner=self.owner, status=FileJob.Status.PENDING)
        resp = self.client.get(f"/manta/jobs/{job.pk}/")
        self.assertContains(resp, "Processing")
        self.assertContains(resp, "fa-spinner")
        self.assertContains(resp, f"/manta/jobs/{job.pk}/status/")   # poll target
        self.assertNotContains(resp, "Result")                       # no result box yet

    def test_job_detail_shows_result_when_done(self):
        job = FileJob.objects.create(command="compress", owner=self.owner,
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

    # These patch `apply_async`, which is what `views.py:410` actually calls.
    # They patched `.delay` until 2026-08 — so `delay.called` could never be
    # true, `test_run_ffmpeg_creates_job` failed, and its two siblings let the
    # REAL apply_async run while asserting nothing. Nobody noticed because
    # `toto.manta.tests` is named in no gate.
    def test_run_ffmpeg_creates_job(self):
        with patch("toto.manta.tasks_direct.run_direct_job.apply_async") as delay:
            delay.return_value.id = "task-compress"
            resp = self.client.post(self.URL, {"file": self.src.pk, "op": "compress", "action": "run",
                                               "quality": "medium", "output_name": "out"})
        job = FileJob.objects.filter(command="compress").last()
        self.assertEqual(job.inputs, [self.src.pk])
        self.assertTrue(delay.called)
        self.assertRedirects(resp, reverse("manta:job_detail", args=[job.pk]), fetch_redirect_response=False)

    def test_run_concat_with_extra(self):
        with patch("toto.manta.tasks_direct.run_direct_job.apply_async") as enqueued:
            enqueued.return_value.id = "task-concat"
            self.client.post(self.URL, {"file": self.src.pk, "op": "concat", "action": "run",
                                        "output_name": "merged", "videos": [self.extra.pk]})
        job = FileJob.objects.filter(command="concat").last()
        self.assertEqual(job.inputs, [self.src.pk, self.extra.pk])

    def test_unauthorized_extra_rejected(self):
        before = FileJob.objects.count()
        with patch("toto.manta.tasks_direct.run_direct_job.apply_async") as delay:
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



class GearPreferenceTests(TestCase):
    """Manta remembers which Compute Gear you send jobs to.

    It used to ask on every form, and the answer is almost never different from
    last time — you hold a Gear for days and push a dozen conversions through
    it. `toto.ambrosia.gears` made the same call for the labs and stored it on
    the workspace; manta has no workspace, so it hangs on the user.

    The property that matters is that remembering is a CONVENIENCE and never an
    authorisation: a remembered Gear still goes through `require_gear` on every
    submit, so one that was released, expired, or never belonged to this person
    is refused at the button.
    """

    def setUp(self):
        self.user = User.objects.create_user("gearowner", password="pass")
        self.other = User.objects.create_user("someone", password="pass")

    def test_nothing_remembered_means_automatic(self):
        from .models import GearPreference

        self.assertEqual(GearPreference.for_user(self.user), "")

    def test_a_choice_is_remembered_as_a_string(self):
        """A string, because that is what a select round-trips — a UUID object
        compared to an option value in a template silently never matches."""
        import uuid

        from .models import GearPreference

        chosen = uuid.uuid4()
        GearPreference.remember(self.user, chosen)
        remembered = GearPreference.for_user(self.user)
        self.assertEqual(remembered, str(chosen))
        self.assertIsInstance(remembered, str)

    def test_choosing_again_replaces_rather_than_duplicates(self):
        import uuid

        from .models import GearPreference

        GearPreference.remember(self.user, uuid.uuid4())
        second = uuid.uuid4()
        GearPreference.remember(self.user, second)
        self.assertEqual(GearPreference.objects.filter(user=self.user).count(), 1)
        self.assertEqual(GearPreference.for_user(self.user), str(second))

    def test_going_back_to_automatic_sticks(self):
        """Automatic has to be rememberable too, or the setting is one-way."""
        import uuid

        from .models import GearPreference

        GearPreference.remember(self.user, uuid.uuid4())
        GearPreference.remember(self.user, None)
        self.assertEqual(GearPreference.for_user(self.user), "")

    def test_one_person_s_choice_is_not_another_s(self):
        import uuid

        from .models import GearPreference

        GearPreference.remember(self.user, uuid.uuid4())
        self.assertEqual(GearPreference.for_user(self.other), "")

    def test_an_anonymous_caller_remembers_nothing_and_is_not_an_error(self):
        from django.contrib.auth.models import AnonymousUser

        from .models import GearPreference

        GearPreference.remember(AnonymousUser(), None)
        self.assertEqual(GearPreference.for_user(AnonymousUser()), "")
        self.assertEqual(GearPreference.objects.count(), 0)
