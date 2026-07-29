from __future__ import annotations

import os
import tempfile

from toto.fileservices.plugin import FileServicePlugin
from toto.fileservices.runner import save_output, stage_input


def _ms_to_srt(ms: int) -> str:
    seconds, milli = divmod(int(ms or 0), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milli:03}"


@FileServicePlugin.plugin(key="transcription", title="Transcription (speech → text)", order=40)
class TranscriptionFileServicePlugin(FileServicePlugin):
    # Backend-only: the menu entry + UI live in the manta builder (transcribe
    # command); this plugin just runs whisper via FileServiceRun.
    listed = False
    accepted_file_types = ["audio"]
    icon = "fa-solid fa-wave-square"
    description = "Transcribe speech to text with Whisper. Saves a .txt transcript and an .srt subtitle file."
    args_label = "Language code (optional)"
    args_placeholder = "en    (leave blank to auto-detect)"
    args_required = False

    def execute(self, run) -> list[int]:
        from toto.transcription.services import transcribe_demo_file

        language = (run.args or "").strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = stage_input(run.input_file, tmpdir)
            result = transcribe_demo_file(local_path, language=language)

            text = result.get("text", "").strip()
            segments = result.get("segments", []) or []
            run.stdout = f"Transcribed {len(segments)} segment(s), {len(text)} characters (engine={result.get('engine', '')})."

            base = os.path.splitext(run.input_file.title or "transcript")[0]
            out_pks: list[int] = []

            # Plain text transcript
            txt_name = f"{base}.txt"
            txt_path = os.path.join(tmpdir, txt_name)
            with open(txt_path, "w", encoding="utf-8") as fh:
                fh.write(text)
            out_pks.append(save_output(run, txt_path, txt_name, "text").pk)

            # SRT subtitle (when timestamped segments are present)
            if any(s.get("end_ms") for s in segments):
                srt_name = f"{base}.srt"
                srt_path = os.path.join(tmpdir, srt_name)
                with open(srt_path, "w", encoding="utf-8") as fh:
                    for idx, s in enumerate(segments, start=1):
                        fh.write(f"{idx}\n{_ms_to_srt(s.get('start_ms', 0))} --> {_ms_to_srt(s.get('end_ms', 0))}\n{s.get('text', '').strip()}\n\n")
                out_pks.append(save_output(run, srt_path, srt_name, "text").pk)

            return out_pks
