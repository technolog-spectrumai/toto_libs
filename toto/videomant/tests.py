"""
videomant test suite.

Coverage:
  - builders: argv shape, no shell tokens
  - client: validate_argv safety, progress parsing
  - runner: probe flow, transcode status transitions (mocked subprocess)
  - workflow: resolver, output envelope
  - tasks: async dispatch sentinel, complete_predefined_node_run
  - views: login required, job_list, job_detail, status.json
"""

import json
from io import StringIO
from unittest.mock import MagicMock, patch, call

from django.contrib.auth.models import User
from django.test import TestCase, Client as DjangoClient, SimpleTestCase, override_settings

from toto.workflows.models import Workflow, WorkflowNode, WorkflowRun, WorkflowNodeRun

from .builders import (
    build_probe, build_compress, build_resize, build_cut,
    build_extract_mp3, build_thumbnail, build_gif_palette, build_gif, build_concat,
)
from .client import FFmpegClient, validate_argv, _parse_progress_line
from .models import MediaJob, ProbeResult


# ---------------------------------------------------------------------------
# Builder tests
# ---------------------------------------------------------------------------

class BuilderArgvTests(SimpleTestCase):
    def test_probe_starts_with_ffprobe(self):
        argv = build_probe("/tmp/a.mp4")
        self.assertEqual(argv[0], "ffprobe")
        self.assertIn("/tmp/a.mp4", argv)
        self.assertIn("-print_format", argv)
        self.assertIn("json", argv)

    def test_compress_defaults(self):
        argv = build_compress("/tmp/in.mp4", "/tmp/out.mp4")
        self.assertEqual(argv[0], "ffmpeg")
        self.assertIn("libx264", argv)
        self.assertIn("23", argv)           # medium CRF
        self.assertIn("/tmp/out.mp4", argv)

    def test_compress_quality_tiny(self):
        argv = build_compress("/tmp/in.mp4", "/tmp/out.mp4", quality="tiny")
        self.assertIn("35", argv)

    def test_compress_quality_archive(self):
        argv = build_compress("/tmp/in.mp4", "/tmp/out.mp4", quality="archive")
        self.assertIn("12", argv)

    def test_resize_default_scale(self):
        # default: both -2 (auto) — caller must pass at least one real dimension via form
        argv = build_resize("/tmp/in.mp4", "/tmp/out.mp4")
        self.assertTrue(any("-2:-2" in a for a in argv))

    def test_resize_width_only_preserves_ar(self):
        argv = build_resize("/tmp/in.mp4", "/tmp/out.mp4", width=1280, height=-2)
        self.assertTrue(any("1280:-2" in a for a in argv))

    def test_resize_height_only_preserves_ar(self):
        argv = build_resize("/tmp/in.mp4", "/tmp/out.mp4", width=-2, height=720)
        self.assertTrue(any("-2:720" in a for a in argv))

    def test_resize_custom_scale(self):
        argv = build_resize("/tmp/in.mp4", "/tmp/out.mp4", width=640, height=360)
        self.assertTrue(any("640:360" in a for a in argv))

    def test_cut_has_ss_and_to(self):
        argv = build_cut("/tmp/in.mp4", "/tmp/out.mp4", "00:00:05", "00:00:15")
        self.assertIn("-ss", argv)
        self.assertIn("00:00:05", argv)
        self.assertIn("-to", argv)
        self.assertIn("00:00:15", argv)

    def test_extract_mp3_default_bitrate(self):
        argv = build_extract_mp3("/tmp/in.mp4", "/tmp/out.mp3")
        self.assertIn("libmp3lame", argv)
        self.assertIn("192k", argv)
        self.assertIn("-vn", argv)

    def test_thumbnail_single_frame(self):
        argv = build_thumbnail("/tmp/in.mp4", "/tmp/out.jpg", at_time="00:00:02")
        self.assertIn("-vframes", argv)
        self.assertIn("1", argv)
        self.assertIn("00:00:02", argv)

    def test_gif_palette_vf_contains_palettegen(self):
        argv = build_gif_palette("/tmp/in.mp4", "/tmp/pal.png", fps=10, width=320)
        self.assertTrue(any("palettegen" in a for a in argv))

    def test_gif_lavfi_contains_paletteuse(self):
        argv = build_gif("/tmp/in.mp4", "/tmp/pal.png", "/tmp/out.gif", fps=10, width=320)
        self.assertTrue(any("paletteuse" in a for a in argv))

    def test_concat_copy_mode(self):
        argv = build_concat("/tmp/list.txt", "/tmp/out.mp4", reencode=False)
        self.assertIn("-c", argv)
        self.assertIn("copy", argv)

    def test_concat_reencode_mode(self):
        argv = build_concat("/tmp/list.txt", "/tmp/out.mp4", reencode=True)
        self.assertIn("libx264", argv)

    def test_all_builders_return_list_of_strings(self):
        checks = [
            build_probe("/tmp/a.mp4"),
            build_compress("/tmp/a.mp4", "/tmp/b.mp4"),
            build_resize("/tmp/a.mp4", "/tmp/b.mp4"),
            build_cut("/tmp/a.mp4", "/tmp/b.mp4", "00:00:00", "00:00:05"),
            build_extract_mp3("/tmp/a.mp4", "/tmp/b.mp3"),
            build_thumbnail("/tmp/a.mp4", "/tmp/b.jpg"),
            build_gif_palette("/tmp/a.mp4", "/tmp/p.png"),
            build_gif("/tmp/a.mp4", "/tmp/p.png", "/tmp/b.gif"),
            build_concat("/tmp/l.txt", "/tmp/b.mp4"),
        ]
        for argv in checks:
            with self.subTest(argv=argv[:2]):
                self.assertIsInstance(argv, list)
                self.assertTrue(all(isinstance(a, str) for a in argv))

    def test_no_builder_output_contains_shell_tokens(self):
        from .client import _SHELL_TOKENS
        # build_gif uses ';' as an ffmpeg filtergraph separator — safe with shell=False.
        # All other builders must be clean.
        _GIF_EXEMPT = {build_gif_palette, build_gif}
        all_cases = [
            (build_probe("/tmp/a.mp4"), set()),
            (build_compress("/tmp/a.mp4", "/tmp/b.mp4"), set()),
            (build_resize("/tmp/a.mp4", "/tmp/b.mp4"), set()),
            (build_cut("/tmp/a.mp4", "/tmp/b.mp4", "00:00:00", "00:00:10"), set()),
            (build_extract_mp3("/tmp/a.mp4", "/tmp/b.mp3"), set()),
            (build_thumbnail("/tmp/a.mp4", "/tmp/b.jpg"), set()),
            (build_gif_palette("/tmp/a.mp4", "/tmp/p.png"), {";"}),
            (build_gif("/tmp/a.mp4", "/tmp/p.png", "/tmp/b.gif"), {";"}),
            (build_concat("/tmp/l.txt", "/tmp/b.mp4"), set()),
        ]
        for argv, exempt in all_cases:
            for arg in argv:
                for token in _SHELL_TOKENS - exempt:
                    self.assertNotIn(token, arg, f"Shell token {token!r} found in {arg!r}")


# ---------------------------------------------------------------------------
# Client safety tests
# ---------------------------------------------------------------------------

class ResizeFormTests(SimpleTestCase):
    def _post(self, data):
        from .forms import ResizeForm
        return ResizeForm(data, prefix="resize")

    def test_preserve_with_width_only_valid(self):
        form = self._post({"resize-preserve_aspect_ratio": "on", "resize-width": "1280", "resize-output_name": "out"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["width"], 1280)
        self.assertEqual(form.cleaned_data["height"], -2)

    def test_preserve_with_height_only_valid(self):
        form = self._post({"resize-preserve_aspect_ratio": "on", "resize-height": "720", "resize-output_name": "out"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["width"], -2)
        self.assertEqual(form.cleaned_data["height"], 720)

    def test_preserve_with_both_dimensions_invalid(self):
        form = self._post({
            "resize-preserve_aspect_ratio": "on",
            "resize-width": "1280",
            "resize-height": "720",
            "resize-output_name": "out",
        })
        self.assertFalse(form.is_valid())
        self.assertTrue(any("one dimension" in e for e in form.non_field_errors()))

    def test_preserve_with_neither_dimension_invalid(self):
        form = self._post({"resize-preserve_aspect_ratio": "on", "resize-output_name": "out"})
        self.assertFalse(form.is_valid())
        self.assertTrue(any("either width or height" in e for e in form.non_field_errors()))

    def test_no_preserve_both_required(self):
        form = self._post({"resize-output_name": "out"})
        self.assertFalse(form.is_valid())
        self.assertIn("width", form.errors)
        self.assertIn("height", form.errors)

    def test_no_preserve_with_both_valid(self):
        form = self._post({"resize-width": "640", "resize-height": "360", "resize-output_name": "out"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["width"], 640)
        self.assertEqual(form.cleaned_data["height"], 360)


class ValidateArgvTests(SimpleTestCase):
    def test_valid_argv_passes(self):
        validate_argv(["ffmpeg", "-i", "/tmp/in.mp4", "/tmp/out.mp4"])

    def test_non_list_raises_type_error(self):
        with self.assertRaises(TypeError):
            validate_argv("ffmpeg -i in.mp4")

    def test_non_string_element_raises(self):
        with self.assertRaises(TypeError):
            validate_argv(["ffmpeg", 42])

    def test_pipe_rejected(self):
        with self.assertRaises(ValueError, msg="| should be rejected"):
            validate_argv(["ffmpeg", "|", "grep"])

    def test_semicolon_rejected(self):
        with self.assertRaises(ValueError):
            validate_argv(["ffmpeg", "-i", "in.mp4; rm -rf /"])

    def test_ampersand_rejected(self):
        with self.assertRaises(ValueError):
            validate_argv(["ffmpeg", "&&", "evil"])

    def test_redirect_rejected(self):
        with self.assertRaises(ValueError):
            validate_argv(["ffmpeg", ">", "/etc/passwd"])

    def test_backtick_rejected(self):
        with self.assertRaises(ValueError):
            validate_argv(["ffmpeg", "`rm -rf /`"])

    def test_dollar_paren_rejected(self):
        with self.assertRaises(ValueError):
            validate_argv(["ffmpeg", "$(cat /etc/passwd)"])


# ---------------------------------------------------------------------------
# Progress parsing tests
# ---------------------------------------------------------------------------

class ProgressParseTests(SimpleTestCase):
    def test_out_time_ms_parsed(self):
        result = _parse_progress_line("out_time_ms=5000000")
        self.assertEqual(result, ("out_time_ms", "5000000"))

    def test_progress_continue(self):
        result = _parse_progress_line("progress=continue")
        self.assertEqual(result, ("progress", "continue"))

    def test_progress_end(self):
        result = _parse_progress_line("progress=end")
        self.assertEqual(result, ("progress", "end"))

    def test_non_progress_line_returns_key_value(self):
        result = _parse_progress_line("frame=120")
        self.assertEqual(result, ("frame", "120"))

    def test_empty_line_returns_none(self):
        result = _parse_progress_line("")
        self.assertIsNone(result)

    def test_line_without_equals_returns_none(self):
        result = _parse_progress_line("no_equals_here")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# FFmpegClient tests (mocked subprocess)
# ---------------------------------------------------------------------------

class FFmpegClientRunProbeTests(SimpleTestCase):
    @patch("toto.videomant.client.subprocess.run")
    def test_run_probe_calls_subprocess_run(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"format":{}}', stderr="")
        client = FFmpegClient()
        result = client.run_probe(["ffprobe", "-v", "quiet", "/tmp/a.mp4"])
        mock_run.assert_called_once()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '{"format":{}}')

    @patch("toto.videomant.client.subprocess.run")
    def test_run_probe_rejects_shell_tokens(self, mock_run):
        client = FFmpegClient()
        with self.assertRaises(ValueError):
            client.run_probe(["ffprobe", "|", "evil"])
        mock_run.assert_not_called()


class FFmpegClientRunTests(SimpleTestCase):
    def _make_mock_proc(self, stdout_lines=None, returncode=0):
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = iter(stdout_lines or [])
        proc.stderr = iter([])
        proc.wait = MagicMock(return_value=None)
        return proc

    @patch("toto.videomant.client.subprocess.Popen")
    def test_run_uses_popen_not_run(self, mock_popen):
        proc = self._make_mock_proc(stdout_lines=[])
        mock_popen.return_value.__enter__ = MagicMock(return_value=proc)
        mock_popen.return_value = proc
        client = FFmpegClient()
        result = client.run(["ffmpeg", "-i", "/tmp/in.mp4", "/tmp/out.mp4"])
        mock_popen.assert_called_once()
        self.assertEqual(result.returncode, 0)

    @patch("toto.videomant.client.subprocess.Popen")
    def test_progress_callback_called(self, mock_popen):
        lines = [
            "frame=10\n",
            "out_time_ms=5000000\n",
            "progress=continue\n",
        ]
        proc = self._make_mock_proc(stdout_lines=lines)
        mock_popen.return_value = proc
        callbacks = []

        def cb(pct, msg):
            callbacks.append(pct)

        client = FFmpegClient()
        client.run(["ffmpeg", "/tmp/out.mp4"], progress_callback=cb, duration_seconds=10.0)
        # out_time_ms=5000000 → 5s, duration=10s → 50%
        self.assertTrue(any(p == 50 for p in callbacks))

    @patch("toto.videomant.client.subprocess.Popen")
    def test_run_rejects_shell_tokens(self, mock_popen):
        client = FFmpegClient()
        with self.assertRaises(ValueError):
            client.run(["ffmpeg", "|", "evil"])
        mock_popen.assert_not_called()

    @patch("toto.videomant.client.subprocess.Popen")
    def test_progress_capped_at_99_during_run(self, mock_popen):
        lines = [
            "out_time_ms=9999999999\n",  # very large
            "progress=continue\n",
        ]
        proc = self._make_mock_proc(stdout_lines=lines)
        mock_popen.return_value = proc
        callbacks = []

        client = FFmpegClient()
        client.run(
            ["ffmpeg", "/tmp/out.mp4"],
            progress_callback=lambda p, m: callbacks.append(p),
            duration_seconds=10.0,
        )
        self.assertTrue(all(p <= 99 for p in callbacks))


# ---------------------------------------------------------------------------
# Runner tests (mocked subprocess + VaultFile)
# ---------------------------------------------------------------------------

PROBE_JSON = json.dumps({
    "format": {"duration": "10.0", "filename": "/tmp/a.mp4"},
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
        {"codec_type": "audio", "codec_name": "aac"},
    ],
})


@override_settings(VIDEOMANT_WORK_ROOT="/tmp/videomant_test", MEDIA_ROOT="/tmp/media_test")
class RunnerProbeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rtest", password="x")

    def _make_input_vf(self):
        from toto.vault.models import VaultFile
        from django.core.files.base import ContentFile
        vf = VaultFile(owner=self.user, title="test.mp4", file_type="video")
        vf.file.save("test.mp4", ContentFile(b"fake"), save=True)
        return vf

    @patch("toto.videomant.client.subprocess.run")
    def test_probe_creates_probe_result(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=PROBE_JSON, stderr="")
        vf = self._make_input_vf()
        job = MediaJob.objects.create(
            task_name="videomant.probe",
            owner=self.user,
            input_file=vf,
        )
        from .runner import MediaJobRunner
        runner = MediaJobRunner()
        runner.run(job)
        job.refresh_from_db()
        self.assertEqual(job.status, MediaJob.Status.SUCCEEDED)
        self.assertEqual(job.progress_percent, 100)
        self.assertTrue(ProbeResult.objects.filter(job=job).exists())
        pr = ProbeResult.objects.get(job=job)
        self.assertAlmostEqual(pr.duration_seconds, 10.0)
        self.assertEqual(pr.video_codec, "h264")
        self.assertEqual(pr.width, 1280)

    @patch("toto.videomant.client.subprocess.run")
    def test_probe_failed_marks_job_failed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        vf = self._make_input_vf()
        job = MediaJob.objects.create(
            task_name="videomant.probe",
            owner=self.user,
            input_file=vf,
        )
        from .runner import MediaJobRunner
        runner = MediaJobRunner()
        with self.assertRaises(RuntimeError):
            runner.run(job)
        job.refresh_from_db()
        self.assertEqual(job.status, MediaJob.Status.FAILED)


@override_settings(VIDEOMANT_WORK_ROOT="/tmp/videomant_test", MEDIA_ROOT="/tmp/media_test")
class RunnerTranscodeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ttest", password="x")

    def _make_input_vf(self):
        from toto.vault.models import VaultFile
        from django.core.files.base import ContentFile
        import tempfile, os
        os.makedirs("/tmp/media_test/vault/files", exist_ok=True)
        vf = VaultFile(owner=self.user, title="test.mp4", file_type="video")
        vf.file.save("test.mp4", ContentFile(b"fakedata"), save=True)
        return vf

    @patch("toto.videomant.runner._probe_duration", return_value=10.0)
    @patch("toto.videomant.client.subprocess.Popen")
    def test_compress_succeeds(self, mock_popen, mock_probe):
        from toto.vault.models import VaultFile
        from django.core.files.base import ContentFile
        import os
        os.makedirs("/tmp/media_test/vault/files", exist_ok=True)
        out_vf = VaultFile(owner=self.user, title="out.mp4", file_type="video")
        out_vf.file.save("out.mp4", ContentFile(b"x"), save=True)

        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = iter(["progress=end\n"])
        proc.stderr = iter([])
        proc.wait = MagicMock()
        mock_popen.return_value = proc

        vf = self._make_input_vf()
        job = MediaJob.objects.create(
            task_name="videomant.compress",
            owner=self.user,
            input_file=vf,
            params={"quality": "medium", "output_name": "out"},
        )
        with patch("toto.videomant.runner.os.path.exists", return_value=True), \
             patch("toto.videomant.runner._save_vault_file", return_value=out_vf):
            from .runner import MediaJobRunner
            runner = MediaJobRunner()
            runner.run(job)

        job.refresh_from_db()
        self.assertEqual(job.status, MediaJob.Status.SUCCEEDED)
        self.assertEqual(job.progress_percent, 100)

    @patch("toto.videomant.runner._probe_duration", return_value=10.0)
    @patch("toto.videomant.client.subprocess.Popen")
    def test_compress_failure_marks_job_failed(self, mock_popen, mock_probe):
        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = iter([])
        proc.stderr = iter(["error: codec not found\n"])
        proc.wait = MagicMock()
        mock_popen.return_value = proc

        vf = self._make_input_vf()
        job = MediaJob.objects.create(
            task_name="videomant.compress",
            owner=self.user,
            input_file=vf,
            params={"quality": "medium"},
        )
        from .runner import MediaJobRunner
        runner = MediaJobRunner()
        with self.assertRaises(RuntimeError):
            runner.run(job)
        job.refresh_from_db()
        self.assertEqual(job.status, MediaJob.Status.FAILED)
        self.assertIn("ffmpeg exited", job.error_message)


# ---------------------------------------------------------------------------
# Workflow resolver tests
# ---------------------------------------------------------------------------

class WorkflowResolverTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("wrtest", password="x")
        self.wf = Workflow.objects.create(name="Test WF")
        self.wf_run = WorkflowRun.objects.create(
            workflow=self.wf,
            input_data={"video": {"kind": "vault_file", "id": 1}},
        )
        self.node = WorkflowNode.objects.create(
            workflow=self.wf,
            node_type=WorkflowNode.PREDEFINED_TASK,
            label="cut_node",
            task_name="videomant.cut",
        )
        self.node_run = WorkflowNodeRun.objects.create(
            workflow_run=self.wf_run,
            node=self.node,
        )

    def test_resolve_literal_passthrough(self):
        from .workflow import resolve_value
        self.assertEqual(resolve_value("hello", self.wf_run, self.node_run), "hello")
        self.assertEqual(resolve_value(42, self.wf_run, self.node_run), 42)

    def test_resolve_workflow_input_non_vault(self):
        from .workflow import resolve_value
        self.wf_run.input_data = {"label": "test_label"}
        self.wf_run.save()
        result = resolve_value("$.workflow.input.label", self.wf_run, self.node_run)
        self.assertEqual(result, "test_label")

    def test_resolve_workflow_input_vault_file(self):
        from .workflow import resolve_value
        from toto.vault.models import VaultFile
        from django.core.files.base import ContentFile
        import os
        os.makedirs("/tmp/media_test/vault/files", exist_ok=True)
        vf = VaultFile(owner=self.user, title="vid.mp4", file_type="video")
        vf.file.save("vid.mp4", ContentFile(b"x"), save=True)
        self.wf_run.input_data = {"video": {"kind": "vault_file", "id": vf.id}}
        self.wf_run.save()
        result = resolve_value("$.workflow.input.video", self.wf_run, self.node_run)
        self.assertIsInstance(result, VaultFile)
        self.assertEqual(result.id, vf.id)

    def test_resolve_nodes_outputs(self):
        from .workflow import resolve_value
        other_node = WorkflowNode.objects.create(
            workflow=self.wf,
            node_type=WorkflowNode.PREDEFINED_TASK,
            label="prev_node",
            task_name="videomant.probe",
        )
        other_run = WorkflowNodeRun.objects.create(
            workflow_run=self.wf_run,
            node=other_node,
            output_data={"data": {"outputs": {"main": {"kind": "vault_file", "id": 7}}}},
        )
        from toto.vault.models import VaultFile
        from django.core.files.base import ContentFile
        import os
        os.makedirs("/tmp/media_test/vault/files", exist_ok=True)
        vf = VaultFile(owner=self.user, title="prev.mp4", file_type="video")
        vf.file.save("prev.mp4", ContentFile(b"x"), save=True)
        other_run.output_data = {"data": {"outputs": {"main": {"kind": "vault_file", "id": vf.id}}}}
        other_run.save()

        result = resolve_value("$.nodes.prev_node.outputs.main", self.wf_run, self.node_run)
        self.assertIsInstance(result, VaultFile)

    def test_unknown_path_raises(self):
        from .workflow import resolve_value
        with self.assertRaises(ValueError):
            resolve_value("$.unknown.path.here", self.wf_run, self.node_run)


# ---------------------------------------------------------------------------
# Workflow output envelope tests
# ---------------------------------------------------------------------------

class OutputEnvelopeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("envtest", password="x")

    def test_envelope_shape_without_output_file(self):
        from .workflow import make_output_envelope
        job = MediaJob(task_name="videomant.probe", progress_percent=100, output_metadata={})
        job.id = 1
        job.output_file_id = None
        result = make_output_envelope(job)
        self.assertIn("data", result)
        data = result["data"]
        self.assertTrue(data["ok"])
        self.assertEqual(data["kind"], "media")
        self.assertEqual(data["task"], "videomant.probe")
        self.assertEqual(data["outputs"], {})

    def test_envelope_shape_with_output_file(self):
        from .workflow import make_output_envelope
        from toto.vault.models import VaultFile
        from django.core.files.base import ContentFile
        import os
        os.makedirs("/tmp/media_test/vault/files", exist_ok=True)
        vf = VaultFile(owner=self.user, title="out.mp4", file_type="video")
        vf.file.save("out.mp4", ContentFile(b"x"), save=True)

        job = MediaJob(task_name="videomant.compress", progress_percent=100, output_metadata={})
        job.id = 5
        job.output_file = vf
        job.output_file_id = vf.id
        result = make_output_envelope(job)
        data = result["data"]
        self.assertIn("main", data["outputs"])
        main = data["outputs"]["main"]
        self.assertEqual(main["kind"], "vault_file")
        self.assertEqual(main["id"], vf.id)


# ---------------------------------------------------------------------------
# predefined_tasks celery registry tests
# ---------------------------------------------------------------------------

class PredefinedTasksCeleryRegistryTests(SimpleTestCase):
    def test_register_celery_and_get(self):
        from toto.workflows import predefined_tasks
        predefined_tasks.register_celery("test.dummy", "toto.dummy.tasks.run")
        result = predefined_tasks.get_celery_task("test.dummy")
        self.assertEqual(result, "toto.dummy.tasks.run")

    def test_get_unknown_returns_none(self):
        from toto.workflows import predefined_tasks
        self.assertIsNone(predefined_tasks.get_celery_task("totally.unknown.task.xyz"))


# ---------------------------------------------------------------------------
# Executor async dispatch tests
# ---------------------------------------------------------------------------

class ExecutorAsyncDispatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("extest", password="x")
        self.wf = Workflow.objects.create(name="Dispatch WF")
        self.node = WorkflowNode.objects.create(
            workflow=self.wf,
            node_type=WorkflowNode.PREDEFINED_TASK,
            label="vm_cut",
            task_name="videomant.cut",
        )

    def test_async_dispatch_saves_celery_task_id(self):
        from toto.workflows.predefined_tasks import register_celery
        register_celery("videomant.cut", "toto.videomant.tasks.cut")

        from toto.workflows.services.executor import WorkflowExecutor
        wf_run = WorkflowRun.objects.create(workflow=self.wf)
        node_run = WorkflowNodeRun.objects.create(
            workflow_run=wf_run,
            node=self.node,
            input_data={},
        )

        fake_result = MagicMock()
        fake_result.id = "celery-task-abc-123"

        with patch("celery.current_app.send_task", return_value=fake_result) as mock_send:
            executor = WorkflowExecutor()
            executor._run_predefined_task(node_run)
            mock_send.assert_called_once_with("toto.videomant.tasks.cut", args=[node_run.id])

        node_run.refresh_from_db()
        self.assertEqual(node_run.celery_task_id, "celery-task-abc-123")
        self.assertEqual(node_run.status, WorkflowNodeRun.RUNNING)

    def test_complete_predefined_node_run_marks_completed(self):
        from toto.workflows.services.executor import WorkflowExecutor
        wf_run = WorkflowRun.objects.create(workflow=self.wf)
        node_run = WorkflowNodeRun.objects.create(
            workflow_run=wf_run,
            node=self.node,
            status=WorkflowNodeRun.RUNNING,
            input_data={},
        )
        executor = WorkflowExecutor()
        output = {"data": {"outputs": {"main": None}, "ok": True}}
        executor.complete_predefined_node_run(node_run.id, output)
        node_run.refresh_from_db()
        self.assertEqual(node_run.status, WorkflowNodeRun.COMPLETED)
        self.assertEqual(node_run.output_data["data"]["ok"], True)


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------

@override_settings(VIDEOMANT_WORK_ROOT="/tmp/videomant_test", MEDIA_ROOT="/tmp/media_test")
class ViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("vtest", password="pass", is_superuser=True)
        self.client = DjangoClient()
        self.client.login(username="vtest", password="pass")
        patcher = patch("toto.ui.page.PageProcessor._get_config", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_vf(self):
        from toto.vault.models import VaultFile
        from django.core.files.base import ContentFile
        import os
        os.makedirs("/tmp/media_test/vault/files", exist_ok=True)
        vf = VaultFile(owner=self.user, title="v.mp4", file_type="video")
        vf.file.save("v.mp4", ContentFile(b"x"), save=True)
        return vf

    def _make_job(self, task="videomant.probe"):
        vf = self._make_vf()
        return MediaJob.objects.create(task_name=task, owner=self.user, input_file=vf)

    def test_job_list_requires_login(self):
        c = DjangoClient()
        resp = c.get("/videomant/")
        self.assertIn(resp.status_code, [302, 301])

    def test_job_list_renders(self):
        self._make_job()
        resp = self.client.get("/videomant/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "videomant.probe")

    def test_job_detail_renders(self):
        job = self._make_job()
        resp = self.client.get(f"/videomant/jobs/{job.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, str(job.pk))

    def test_job_status_json_returns_correct_fields(self):
        job = self._make_job()
        resp = self.client.get(f"/videomant/jobs/{job.pk}/status.json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("status", data)
        self.assertIn("progress_percent", data)
        self.assertIn("is_terminal", data)
        self.assertEqual(data["id"], job.pk)

    def test_job_status_json_requires_login(self):
        job = self._make_job()
        c = DjangoClient()
        resp = c.get(f"/videomant/jobs/{job.pk}/status.json")
        self.assertIn(resp.status_code, [302, 301])

    def test_vault_file_actions_renders(self):
        vf = self._make_vf()
        resp = self.client.get(f"/videomant/vault/{vf.pk}/actions/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "compress")

    def test_enqueue_compress_creates_job(self):
        vf = self._make_vf()
        before = MediaJob.objects.count()
        with patch("toto.videomant.tasks_direct.run_direct_job.delay"):
            resp = self.client.post(
                f"/videomant/vault/{vf.pk}/compress/",
                {"compress-quality": "medium", "compress-output_name": "out"},
            )
        self.assertEqual(MediaJob.objects.count(), before + 1)
        job = MediaJob.objects.filter(task_name="videomant.compress").last()
        self.assertIsNotNone(job)
        self.assertIsNone(job.workspace)  # no workspace passed → no link
        self.assertRedirects(resp, f"/videomant/jobs/{job.pk}/", fetch_redirect_response=False)

    def test_enqueue_compress_links_workspace(self):
        from toto.vault.models import Bucket
        from .models import Workspace
        bucket = Bucket.objects.create(name="WsBk2", owner=self.user, slug="wsbk2")
        ws = Workspace.objects.create(name="WS Link", bucket=bucket, owner=self.user)
        vf = self._make_vf()
        with patch("toto.videomant.tasks_direct.run_direct_job.delay"):
            self.client.post(
                f"/videomant/vault/{vf.pk}/compress/",
                {"compress-quality": "medium", "compress-output_name": "out", "ws_slug": ws.slug},
            )
        job = MediaJob.objects.filter(task_name="videomant.compress", workspace=ws).last()
        self.assertIsNotNone(job)

    def test_enqueue_concat_uses_primary_plus_extras(self):
        from toto.vault.models import VaultFile
        from django.core.files.base import ContentFile
        import os
        os.makedirs("/tmp/media_test/vault/files", exist_ok=True)
        vf2 = VaultFile(owner=self.user, title="second.mp4", file_type="video")
        vf2.file.save("second.mp4", ContentFile(b"x"), save=True)
        vf = self._make_vf()
        with patch("toto.videomant.tasks_direct.run_direct_job.delay"):
            self.client.post(
                f"/videomant/vault/{vf.pk}/concat/",
                {
                    "concat-reencode": "",
                    "concat-output_name": "merged",
                    "concat-extra_file_ids": str(vf2.pk),
                },
            )
        job = MediaJob.objects.filter(task_name="videomant.concat").last()
        self.assertIsNotNone(job)
        self.assertIn(vf.pk, job.input_files)
        self.assertIn(vf2.pk, job.input_files)

    def test_bucket_list_renders(self):
        from toto.vault.models import Bucket
        Bucket.objects.create(name="TestBucket", owner=self.user, slug="testbucket")
        resp = self.client.get("/videomant/browse/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "TestBucket")

    def test_bucket_file_list_renders(self):
        from toto.vault.models import Bucket
        bucket = Bucket.objects.create(name="MediaBucket", owner=self.user, slug="mediabucket")
        vf = self._make_vf()
        vf.bucket = bucket
        vf.save()
        resp = self.client.get(f"/videomant/browse/{bucket.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "MediaBucket")

    def test_bucket_file_list_search(self):
        from toto.vault.models import Bucket
        bucket = Bucket.objects.create(name="SearchBucket", owner=self.user, slug="searchbucket")
        vf = self._make_vf()
        vf.bucket = bucket
        vf.title = "UniqueTitle999"
        vf.save()
        resp = self.client.get(f"/videomant/browse/{bucket.pk}/?q=UniqueTitle999")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "UniqueTitle999")

    # Workspace views
    def _make_bucket(self, name="TestBucket", slug="testbucket"):
        from toto.vault.models import Bucket
        return Bucket.objects.create(name=name, owner=self.user, slug=slug)

    def test_workspace_list_renders(self):
        resp = self.client.get("/videomant/workspaces/")
        self.assertEqual(resp.status_code, 200)

    def test_workspace_create_blocked_for_non_federal(self):
        regular = User.objects.create_user("notfederal_ws", password="x")
        c = DjangoClient()
        c.login(username="notfederal_ws", password="x")
        patcher = patch("toto.ui.page.PageProcessor._get_config", return_value=None)
        patcher.start()
        try:
            resp = c.post("/videomant/workspaces/create/", {"name": "ws", "bucket_id": "1"})
            self.assertRedirects(resp, "/videomant/workspaces/", fetch_redirect_response=False)
        finally:
            patcher.stop()

    def test_workspace_create_allowed_for_superuser(self):
        bucket = self._make_bucket()
        resp = self.client.post("/videomant/workspaces/create/", {
            "name": "My WS",
            "bucket_id": str(bucket.pk),
            "description": "",
        })
        from .models import Workspace
        self.assertTrue(Workspace.objects.filter(name="My WS").exists())

    def test_workspace_detail_renders(self):
        from .models import Workspace
        bucket = self._make_bucket(name="WsBucket", slug="wsbucket")
        ws = Workspace.objects.create(name="Test WS", bucket=bucket, owner=self.user)
        resp = self.client.get(f"/videomant/workspaces/{ws.slug}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Test WS")

    def test_workspace_slug_auto_generated(self):
        from .models import Workspace
        bucket = self._make_bucket(name="SlugBucket", slug="slugbucket")
        ws = Workspace.objects.create(name="My Workspace", bucket=bucket, owner=self.user)
        self.assertEqual(ws.slug, "my-workspace")

    def test_workspace_access_denied_for_other_user(self):
        from .models import Workspace
        other = User.objects.create_user("other_ws", password="x")
        bucket = self._make_bucket(name="PrivBucket", slug="privbucket")
        ws = Workspace.objects.create(name="Private WS", bucket=bucket, owner=other)
        resp = self.client.get(f"/videomant/workspaces/{ws.slug}/")
        self.assertEqual(resp.status_code, 404)

    def test_bucket_list_requires_login(self):
        c = DjangoClient()
        resp = c.get("/videomant/browse/")
        self.assertIn(resp.status_code, [301, 302])

    def test_enqueue_cut_validates_form(self):
        vf = self._make_vf()
        # missing required fields → stay on actions page
        resp = self.client.post(
            f"/videomant/vault/{vf.pk}/cut/",
            {"cut-output_name": "clip"},  # missing start_time and end_time
        )
        # redirects back to actions on invalid form
        self.assertRedirects(resp, f"/videomant/vault/{vf.pk}/actions/", fetch_redirect_response=False)


# ---------------------------------------------------------------------------
# Command factory (pure)
# ---------------------------------------------------------------------------

class FFmpegCommandFactoryTests(SimpleTestCase):
    def test_compress_shell_display_has_no_paths(self):
        from .factory import FFmpegCommandFactory
        spec = FFmpegCommandFactory().build(
            "compress", input_name="clip.mp4",
            params={"quality": "high", "output_name": "out"},
        )
        self.assertEqual(spec.output_names, ("out.mp4",))
        self.assertIn("-i clip.mp4", spec.shell_display)
        self.assertIn("out.mp4", spec.shell_display)
        self.assertNotIn("/", spec.shell_display)  # display names only, no fs paths

    def test_probe_has_no_outputs(self):
        from .factory import FFmpegCommandFactory
        spec = FFmpegCommandFactory().build("probe", input_name="clip.mp4")
        self.assertEqual(spec.output_names, ())
        self.assertTrue(spec.shell_display.startswith("ffprobe"))

    def test_gif_emits_two_commands(self):
        from .factory import FFmpegCommandFactory
        spec = FFmpegCommandFactory().build(
            "gif", input_name="clip.mp4", params={"output_name": "anim"},
        )
        self.assertEqual(len(spec.commands), 2)  # palette + encode
        self.assertEqual(spec.output_names, ("anim.gif",))


# ---------------------------------------------------------------------------
# Iterative command builder view
# ---------------------------------------------------------------------------

@override_settings(VIDEOMANT_WORK_ROOT="/tmp/videomant_test", MEDIA_ROOT="/tmp/media_test")
class CommandBuilderTests(TestCase):
    BUILDER_URL = "/videomant/vault/{pk}/builder/"

    def setUp(self):
        # curator owns the bucket; owner (viewer) owns files but not the bucket,
        # so non-owner access filtering is actually exercised.
        self.curator = User.objects.create_user("cb_curator", password="pass")
        self.owner = User.objects.create_user("cb_owner", password="pass")
        self.stranger = User.objects.create_user("cb_stranger", password="pass")
        self.client = DjangoClient()
        self.client.login(username="cb_owner", password="pass")
        patcher = patch("toto.ui.page.PageProcessor._get_config", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

        from toto.vault.models import Bucket
        self.bucket = Bucket.objects.create(name="CB Bucket", owner=self.curator, slug="cb-bucket")
        self.other_bucket = Bucket.objects.create(name="Other", owner=self.curator, slug="cb-other")

        self.src = self._vf(self.owner, "source.mp4")
        self.extra = self._vf(self.owner, "second.mp4")
        self.secret = self._vf(self.stranger, "secret.mp4", is_public=False)
        self.foreign = self._vf(self.owner, "foreign.mp4", bucket=self.other_bucket)

    def _vf(self, owner, name, *, file_type="video", bucket=None, is_public=False):
        from toto.vault.models import VaultFile
        from django.core.files.base import ContentFile
        import os
        os.makedirs("/tmp/media_test/vault/files", exist_ok=True)
        vf = VaultFile(
            owner=owner, title=name, file_type=file_type,
            bucket=bucket if bucket is not None else self.bucket,
            is_public=is_public,
        )
        vf.file.save(name, ContentFile(b"x"), save=True)
        return vf

    def _url(self, vf=None):
        return self.BUILDER_URL.format(pk=(vf or self.src).pk)

    # -- access + listing -------------------------------------------------

    def test_builder_shows_source_and_same_bucket_accessible_files(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "source.mp4")   # the selected source file
        self.assertContains(resp, "second.mp4")   # accessible same-bucket file
        self.assertNotContains(resp, "secret.mp4")  # stranger's private file hidden
        self.assertNotContains(resp, "foreign.mp4")  # different bucket hidden

    def test_builder_denies_inaccessible_source(self):
        resp = self.client.get(self._url(self.secret))
        self.assertEqual(resp.status_code, 404)

    # -- preview ----------------------------------------------------------

    def test_preview_renders_command_without_creating_job(self):
        before = MediaJob.objects.count()
        resp = self.client.post(self._url(), {
            "op": "compress",
            "action": "preview",
            "spec_yaml": "quality: medium\noutput_name: compressed\n",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(MediaJob.objects.count(), before)  # no job created
        self.assertContains(resp, "compressed.mp4")

    def test_preview_equals_factory_shell_display(self):
        from .factory import FFmpegCommandFactory
        from .views import display_input_name
        spec = FFmpegCommandFactory().build(
            "compress",
            input_name=display_input_name(self.src),
            params={"quality": "medium", "output_name": "compressed"},
        )
        resp = self.client.post(self._url(), {
            "op": "compress",
            "action": "preview",
            "spec_yaml": "quality: medium\noutput_name: compressed\n",
        })
        self.assertContains(resp, spec.shell_display)

    def test_user_absolute_input_path_is_ignored(self):
        resp = self.client.post(self._url(), {
            "op": "compress",
            "action": "preview",
            "spec_yaml": "quality: medium\noutput_name: compressed\ninput_path: /etc/passwd\n",
        })
        self.assertEqual(resp.status_code, 200)
        # The YAML is echoed back into the editor (value preservation), but the
        # path must never reach the generated command — input comes from the
        # resolved vault file only.
        self.assertNotContains(resp, "-i /etc/passwd")
        self.assertContains(resp, "-i source.mp4")

    # -- run --------------------------------------------------------------

    def test_run_creates_and_enqueues_job(self):
        before = MediaJob.objects.count()
        with patch("toto.videomant.tasks_direct.run_direct_job.delay") as delay:
            resp = self.client.post(self._url(), {
                "op": "compress",
                "action": "run",
                "spec_yaml": "quality: medium\noutput_name: compressed\n",
            })
        self.assertEqual(MediaJob.objects.count(), before + 1)
        job = MediaJob.objects.filter(task_name="videomant.compress").last()
        self.assertIsNotNone(job)
        self.assertTrue(delay.called)
        self.assertRedirects(resp, f"/videomant/jobs/{job.pk}/", fetch_redirect_response=False)

    def test_run_invalid_yaml_creates_no_job(self):
        before = MediaJob.objects.count()
        with patch("toto.videomant.tasks_direct.run_direct_job.delay") as delay:
            resp = self.client.post(self._url(), {
                "op": "compress",
                "action": "run",
                "spec_yaml": "quality: medium\n  bad: : indent",  # malformed YAML
            })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(MediaJob.objects.count(), before)
        self.assertFalse(delay.called)

    def test_run_concat_with_accessible_extra(self):
        with patch("toto.videomant.tasks_direct.run_direct_job.delay"):
            self.client.post(self._url(), {
                "op": "concat",
                "action": "run",
                "spec_yaml": f"reencode: false\noutput_name: merged\nextra_file_ids: [{self.extra.pk}]\n",
            })
        job = MediaJob.objects.filter(task_name="videomant.concat").last()
        self.assertIsNotNone(job)
        self.assertEqual(job.input_files, [self.src.pk, self.extra.pk])

    def test_unauthorized_bucket_file_cannot_be_referenced(self):
        before = MediaJob.objects.count()
        with patch("toto.videomant.tasks_direct.run_direct_job.delay") as delay:
            resp = self.client.post(self._url(), {
                "op": "concat",
                "action": "run",
                "spec_yaml": f"reencode: false\noutput_name: merged\nextra_file_ids: [{self.secret.pk}]\n",
            })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(MediaJob.objects.count(), before)  # rejected, no job
        self.assertFalse(delay.called)
        self.assertContains(resp, "access")

    def test_out_of_bucket_file_cannot_be_referenced(self):
        before = MediaJob.objects.count()
        with patch("toto.videomant.tasks_direct.run_direct_job.delay"):
            resp = self.client.post(self._url(), {
                "op": "concat",
                "action": "run",
                "spec_yaml": f"reencode: false\noutput_name: merged\nextra_file_ids: [{self.foreign.pk}]\n",
            })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(MediaJob.objects.count(), before)
        self.assertContains(resp, "not in this bucket")
