#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent

ROTORS_DIR     = SCRIPT_DIR
ROTOR_CORE_DIR = ROTORS_DIR / "rotor_core"
ROTOR_PY_DIR   = ROTORS_DIR / "rotor_py"
ROTOR_WASM_DIR  = ROTORS_DIR / "rotor_wasm"

# Web build → Django telegraph static files
DEFAULT_WASM_OUT_DIR = SCRIPT_DIR.parent / "toto" / "telegraph" / "static" / "js" / "rotor_wasm"

# Bundler build → Tauri app node_modules (via package.json file: link)
TAURI_WASM_OUT_DIR = ROTOR_WASM_DIR / "build"


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
    require_dir(ROTORS_DIR)
    require_file(ROTORS_DIR / "Cargo.toml")

    require_dir(ROTOR_CORE_DIR)
    require_file(ROTOR_CORE_DIR / "Cargo.toml")
    require_file(ROTOR_CORE_DIR / "src" / "lib.rs")

    require_dir(ROTOR_PY_DIR)
    require_file(ROTOR_PY_DIR / "Cargo.toml")
    require_file(ROTOR_PY_DIR / "src" / "lib.rs")

    require_dir(ROTOR_WASM_DIR)
    require_file(ROTOR_WASM_DIR / "Cargo.toml")
    require_file(ROTOR_WASM_DIR / "src" / "lib.rs")


def build_core() -> None:
    require_tool("cargo")
    run(["cargo", "check", "-p", "rotor-core"], cwd=ROTORS_DIR)


def build_py() -> None:
    require_tool("maturin")
    run(["maturin", "develop"], cwd=ROTOR_PY_DIR)


def smoke_test_py() -> None:
    code = r"""
import rotor_py

alice_state = bytes(rotor_py.RotorEngine.create_group_state("smoke-room", "alice"))
bob_state   = bytes(rotor_py.RotorEngine.create_empty_state("smoke-room", "bob"))

bob_state, bob_key_package = rotor_py.RotorEngine.key_package_from_state(bob_state)

alice_state, welcome, commit = rotor_py.RotorEngine.add_member_from_state(
    bytes(alice_state), bytes(bob_key_package)
)

bob_state = rotor_py.RotorEngine.join_from_welcome_from_state(
    bytes(bob_state), bytes(welcome)
)

alice_state, alice_ct = rotor_py.RotorEngine.encrypt_app_from_state(
    bytes(alice_state), b"hello bob"
)
bob_state, bob_pt = rotor_py.RotorEngine.process_message_from_state(
    bytes(bob_state), bytes(alice_ct)
)
assert bytes(bob_pt) == b"hello bob"

bob_state, bob_ct = rotor_py.RotorEngine.encrypt_app_from_state(
    bytes(bob_state), b"hello alice"
)
alice_state, alice_pt = rotor_py.RotorEngine.process_message_from_state(
    bytes(alice_state), bytes(bob_ct)
)
assert bytes(alice_pt) == b"hello alice"

print("rotor_py smoke E2E OK")
"""
    run([sys.executable, "-c", code], cwd=ROTORS_DIR)


def test_py() -> None:
    test_dir = ROTOR_PY_DIR / "test"
    if not test_dir.exists():
        print(f"Skipping pytest: no test directory at {test_dir}")
        return

    test_files = sorted(test_dir.glob("test_*.py")) + sorted(test_dir.glob("*_test.py"))
    if not test_files:
        print(f"Skipping pytest: no test files found in {test_dir}")
        return

    require_tool("pytest")
    run(["pytest", "-q", "test"], cwd=ROTOR_PY_DIR)


def build_wasm(out_dir: Path, target: str = "web") -> None:
    require_tool("wasm-pack")

    out_dir.mkdir(parents=True, exist_ok=True)

    run(
        [
            "wasm-pack",
            "build",
            "--target",
            target,
            "--out-dir",
            out_dir,
        ],
        cwd=ROTOR_WASM_DIR,
    )


def clean() -> None:
    require_tool("cargo")
    run(["cargo", "clean"], cwd=ROTORS_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build all rotor Rust/Python/WASM components."
    )
    parser.add_argument("--skip-core", action="store_true")
    parser.add_argument("--skip-py", action="store_true")
    parser.add_argument("--skip-wasm", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument(
        "--tauri",
        action="store_true",
        help="Also build a 'bundler' target for the Tauri app into rotor_wasm/build/",
    )
    parser.add_argument(
        "--wasm-out-dir",
        type=Path,
        default=DEFAULT_WASM_OUT_DIR,
        help=f"WASM output directory (web target). Default: {DEFAULT_WASM_OUT_DIR}",
    )

    args = parser.parse_args()

    print(f"Cargo workspace : {ROTORS_DIR}")
    print(f"rotor_core      : {ROTOR_CORE_DIR}")
    print(f"rotor_py        : {ROTOR_PY_DIR}")
    print(f"rotor_wasm       : {ROTOR_WASM_DIR}")
    print(f"WASM out dir    : {args.wasm_out_dir}")
    if args.tauri:
        print(f"Tauri WASM dir  : {TAURI_WASM_OUT_DIR}")

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
        build_wasm(args.wasm_out_dir, target="web")
        if args.tauri:
            build_wasm(TAURI_WASM_OUT_DIR, target="bundler")

    print()
    print("All requested rotor builds completed successfully.")


if __name__ == "__main__":
    main()
