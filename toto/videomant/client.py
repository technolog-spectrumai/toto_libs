import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable

# `;` is a valid ffmpeg filtergraph chain separator (e.g. in -lavfi/-filter_complex) and is
# harmless in subprocess args since shell=False — it is intentionally excluded here.
_SHELL_TOKENS = {"&&", "||", "|", ">", "<", "`", "$("}


def validate_argv(argv: list) -> None:
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be list[str]")
    for arg in argv:
        for token in _SHELL_TOKENS:
            if token in arg:
                raise ValueError(f"Rejected shell token {token!r} in argv arg {arg!r}")


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str


def _parse_progress_line(line: str) -> tuple[str, str] | None:
    """Return (key, value) from a ffmpeg -progress output line, or None."""
    if "=" in line:
        k, _, v = line.partition("=")
        return k.strip(), v.strip()
    return None


class FFmpegClient:
    def run_probe(self, argv: list[str], timeout: int = 30) -> RunResult:
        validate_argv(argv)
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return RunResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def run(
        self,
        argv: list[str],
        timeout: int = 3600,
        progress_callback: Callable[[int, str], None] | None = None,
        duration_seconds: float | None = None,
    ) -> RunResult:
        validate_argv(argv)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def _drain_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        out_time_ms: int | None = None
        try:
            for line in proc.stdout:
                stdout_lines.append(line)
                parsed = _parse_progress_line(line.rstrip())
                if parsed is None:
                    continue
                key, val = parsed
                if key == "out_time_ms" and val.lstrip("-").isdigit():
                    out_time_ms = int(val)
                elif key == "progress" and progress_callback and duration_seconds:
                    current = (out_time_ms or 0) / 1_000_000
                    pct = min(99, int(current / duration_seconds * 100)) if duration_seconds > 0 else 0
                    progress_callback(pct, f"{current:.1f}s / {duration_seconds:.1f}s")
        finally:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            stderr_thread.join(timeout=5)

        return RunResult(
            returncode=proc.returncode,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines),
        )
