from __future__ import annotations

import os
import subprocess
import sys
import tempfile


def run_python_script(vault_file, timeout: int = 30) -> tuple[str, str, int]:
    """
    Execute vault_file content as a Python script in a temp directory.
    Returns (stdout, stderr, exit_code).
    """
    with vault_file.file.open("r") as f:
        source = f.read()

    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = os.path.join(tmpdir, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(source)

        try:
            proc = subprocess.run(
                [sys.executable, script_path],
                cwd=tmpdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
            stdout = proc.stdout.decode(errors="replace")
            stderr = proc.stderr.decode(errors="replace")
            return stdout, stderr, proc.returncode
        except subprocess.TimeoutExpired:
            return "", f"Execution timed out after {timeout}s.", 1
