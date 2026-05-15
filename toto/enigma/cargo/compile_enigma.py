#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent

ENIGMA_CARGO_DIR = SCRIPT_DIR
ENIGMA_CORE_DIR = ENIGMA_CARGO_DIR / "enigma_core"
ENIGMA_PY_DIR = ENIGMA_CARGO_DIR / "enigma_py"
ENIGMA_WASM_DIR = ENIGMA_CARGO_DIR / "enigma_wasm"

DEFAULT_WASM_OUT_DIR = SCRIPT_DIR.parent / "static" / "js" / "enigma_wasm"


def run(
    command: list[str | Path],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> None:
    print()
    print(f"$ cd {cwd}")
    print("$ " + " ".join(str(part) for part in command))
    print()

    subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd),
        env=env,
        check=True,
    )


def require_dir(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Missing directory: {path}")


def require_file(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Missing file: {path}")


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(
            f"Missing required tool: {name}\n"
            f"Install it first, then rerun this script."
        )


def check_layout() -> None:
    require_dir(ENIGMA_CARGO_DIR)
    require_file(ENIGMA_CARGO_DIR / "Cargo.toml")

    require_dir(ENIGMA_CORE_DIR)
    require_file(ENIGMA_CORE_DIR / "Cargo.toml")
    require_file(ENIGMA_CORE_DIR / "src" / "lib.rs")

    require_dir(ENIGMA_PY_DIR)
    require_file(ENIGMA_PY_DIR / "Cargo.toml")
    require_file(ENIGMA_PY_DIR / "src" / "lib.rs")

    require_dir(ENIGMA_WASM_DIR)
    require_file(ENIGMA_WASM_DIR / "Cargo.toml")
    require_file(ENIGMA_WASM_DIR / "src" / "lib.rs")


def build_core() -> None:
    require_tool("cargo")
    run(["cargo", "check", "-p", "enigma-core"], cwd=ENIGMA_CARGO_DIR)


def build_py() -> None:
    require_tool("maturin")
    run(["maturin", "develop"], cwd=ENIGMA_PY_DIR)


def smoke_test_py() -> None:
    code = r"""
import enigma_py

alice = enigma_py.BotSession("candyland", "Alice")
bob = enigma_py.BotSession("candyland", "Bob")

alice.create_group()

bob_key_package = bob.key_package()
welcome, commit = alice.add_member(bob_key_package)

assert isinstance(welcome, bytes)
assert isinstance(commit, bytes)
assert len(welcome) > 0
assert len(commit) > 0

bob.join_from_welcome(welcome)

alice_ciphertext = alice.encrypt_app(b"hello bob")
bob_plaintext = bob.process_message(alice_ciphertext)

assert bob_plaintext == b"hello bob"

bob_ciphertext = bob.encrypt_app(b"hello alice")
alice_plaintext = alice.process_message(bob_ciphertext)

assert alice_plaintext == b"hello alice"

print("enigma_py smoke E2E OK")
"""
    run([sys.executable, "-c", code], cwd=ENIGMA_CARGO_DIR)


def test_py() -> None:
    test_dir = ENIGMA_PY_DIR / "test"
    if not test_dir.exists():
        print(f"Skipping pytest: no test directory at {test_dir}")
        return

    test_files = sorted(test_dir.glob("test_*.py")) + sorted(test_dir.glob("*_test.py"))
    if not test_files:
        print(f"Skipping pytest: no test files found in {test_dir}")
        return

    require_tool("pytest")
    run(["pytest", "-q", "test"], cwd=ENIGMA_PY_DIR)


def build_wasm(out_dir: Path) -> None:
    require_tool("wasm-pack")

    out_dir.mkdir(parents=True, exist_ok=True)

    run(
        [
            "wasm-pack",
            "build",
            "--target",
            "web",
            "--out-dir",
            out_dir,
        ],
        cwd=ENIGMA_WASM_DIR,
    )


def clean() -> None:
    require_tool("cargo")
    run(["cargo", "clean"], cwd=ENIGMA_CARGO_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build all Enigma Rust/Python/WASM components."
    )
    parser.add_argument("--skip-core", action="store_true")
    parser.add_argument("--skip-py", action="store_true")
    parser.add_argument("--skip-wasm", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument(
        "--wasm-out-dir",
        type=Path,
        default=DEFAULT_WASM_OUT_DIR,
        help=f"WASM output directory. Default: {DEFAULT_WASM_OUT_DIR}",
    )

    args = parser.parse_args()

    print(f"Cargo workspace: {ENIGMA_CARGO_DIR}")
    print(f"enigma_core: {ENIGMA_CORE_DIR}")
    print(f"enigma_py: {ENIGMA_PY_DIR}")
    print(f"enigma_wasm: {ENIGMA_WASM_DIR}")
    print(f"WASM out dir: {args.wasm_out_dir}")

    check_layout()

    if args.clean:
        clean()

    if not args.skip_core:
        build_core()

    if not args.skip_py:
        build_py()

    if not args.skip_tests:
        smoke_test_py()
        test_py()

    if not args.skip_wasm:
        build_wasm(args.wasm_out_dir)

    print()
    print("All requested Enigma builds completed successfully.")


if __name__ == "__main__":
    main()