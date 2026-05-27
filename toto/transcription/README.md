# toto.transcription

Oya-style Django app for uploading audio/video files, running local transcription jobs, storing timestamped transcript segments, and exporting TXT / SRT / VTT / JSON.

This version supports both local Whisper engines:

- `openai-whisper` — original OpenAI Whisper package, requires FFmpeg on PATH.
- `faster-whisper` — CTranslate2-based Whisper runtime, good for CPU INT8 and GPU acceleration.

## Install

Copy the folder into your monorepo as:

```text
toto/transcription/
```

Add it in Studio mode:

```python
if BUILD_STUDIO:
    INSTALLED_APPS += [
        "toto.transcription",
    ]
```

Add URLs:

```python
path("transcription/", include("toto.transcription.urls")),
```

Then run:

```bash
python manage.py makemigrations transcription
python manage.py migrate
```

## Python packages

The app assumes you install the local engines you want to use:

```bash
pip install -U openai-whisper faster-whisper
```

For `openai-whisper`, also install FFmpeg and make sure `ffmpeg` is on PATH.

Examples:

```bash
# macOS
brew install ffmpeg

# Windows with Chocolatey
choco install ffmpeg

# Windows with Scoop
scoop install ffmpeg

# Debian / Ubuntu
sudo apt-get install ffmpeg
```

`faster-whisper` uses PyAV and usually does not need a separate FFmpeg executable for normal decoding.

## Engine choices

Every `TranscriptionJob` has an `engine` field:

```text
Default backend
openai-whisper
faster-whisper
Command backend
Custom callable
```

The default engine is controlled with:

```python
TRANSCRIPTION_DEFAULT_ENGINE = "faster_whisper"  # or "openai_whisper"
```

## openai-whisper backend

Use this when you want the original Whisper package and CLI-like behavior.

```python
TRANSCRIPTION_DEFAULT_ENGINE = "openai_whisper"
TRANSCRIPTION_OPENAI_WHISPER_MODEL = "small"  # tiny, base, small, medium, large, turbo
TRANSCRIPTION_OPENAI_WHISPER_DEVICE = None     # None, "cpu", or "cuda"
TRANSCRIPTION_OPENAI_WHISPER_FP16 = None       # None lets whisper decide; False is safer on CPU
```

For CPU-only Windows/Linux, this is usually safer:

```python
TRANSCRIPTION_OPENAI_WHISPER_DEVICE = "cpu"
TRANSCRIPTION_OPENAI_WHISPER_FP16 = False
```

## faster-whisper backend

Use this for better speed, CPU INT8, or NVIDIA GPU acceleration.

```python
TRANSCRIPTION_DEFAULT_ENGINE = "faster_whisper"
TRANSCRIPTION_FASTER_WHISPER_MODEL = "small"       # tiny, base, small, medium, large-v3, turbo, etc.
TRANSCRIPTION_FASTER_WHISPER_DEVICE = "cpu"        # "cpu" or "cuda"
TRANSCRIPTION_FASTER_WHISPER_COMPUTE_TYPE = "int8" # cpu: int8; cuda: float16 is common
TRANSCRIPTION_FASTER_WHISPER_BEAM_SIZE = 5
```

For NVIDIA GPU:

```python
TRANSCRIPTION_FASTER_WHISPER_DEVICE = "cuda"
TRANSCRIPTION_FASTER_WHISPER_COMPUTE_TYPE = "float16"
```

## Translation

Local Whisper supports transcription and translation **to English**. In the job form:

- leave `translate_to` empty for normal transcription;
- set `translate_to = en` for speech-to-English translation.

The service raises a clear error for other target languages because Whisper local backends are not speech-to-any-language translation engines.

## Custom callable backend

```python
TRANSCRIPTION_BACKEND = "myapp.transcription_backends.transcribe"
```

Callable signature:

```python
def transcribe(file_path: str, *, job, language: str = "") -> dict:
    return {
        "segments": [
            {"start_ms": 0, "end_ms": 1200, "speaker": "Speaker 1", "text": "Hello"},
        ]
    }
```

## Command backend

```python
TRANSCRIPTION_COMMAND = ["python", "manage.py", "my_transcriber", "{file}", "--language", "{language}"]
```

The command should print JSON to stdout. Either a list of segments or an object with a `segments` array is accepted.

## Development backend

For UI testing without a speech model:

```python
TRANSCRIPTION_BACKEND = "toto.transcription.backends.sidecar_txt_backend"
```

Place a sidecar text file next to your uploaded media in storage, named like:

```text
recording.mp3.txt
```

## Oya style

Templates extend `oya/base.html`, use `darkMode` Alpine bindings, Tailwind utility classes, Font Awesome icons, reusable `_form.html`, card grids, stats strips, and Chart.js dashboard panels.

## Boundaries

- `vault` stores original audio/video and generated transcript artifacts.
- `transcription` owns transcription collections, sources, jobs, segments, speakers, artifacts, and analytics events.
- AI summarization or action-item extraction should be added later through `steven` or `workflows` rather than mixed into the core STT flow.
