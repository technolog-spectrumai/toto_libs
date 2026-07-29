"""The lockstep version rule, and the gates that enforce it."""
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from toto.versioning import (
    TotoVersionError,
    parse_wheel,
    read_manifest,
    verify_checkout,
    verify_wheels,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION = (REPO_ROOT / "VERSION").read_text().strip()
PYPROJECTS = sorted((REPO_ROOT / "packages").glob("*/pyproject.toml"))


# --- the repository itself ------------------------------------------------


# Build artefacts and caches. Anything else under packages/ that git ignores is a
# source file that will never be committed and so will never reach a wheel.
_IGNORABLE_UNDER_PACKAGES = ("build/", "dist/", "__pycache__/", ".egg-info")


def test_no_source_file_under_packages_is_gitignored():
    """A gitignored source file is invisible twice over: it works locally and ships
    in nothing.

    This exists because of a real one. The shared Media sub-nav was written to
    ``packages/toto-base/src/toto/core/templates/media/_tabs.html`` and rendered
    perfectly from the source tree — but ``.gitignore`` carries ``media/`` for
    MEDIA_ROOT, and a bare directory rule matches at *any* depth, so the file was
    never committed and never packaged. It took a host's clean-env gate, several
    steps later, to surface it as a missing template. It now lives at
    ``oya/_media_tabs.html``.
    """
    done = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--others", "--ignored",
         "--exclude-standard", "--directory", "packages/"],
        capture_output=True, text=True,
    )
    if done.returncode != 0:          # not a git checkout (e.g. a vendored subtree)
        pytest.skip("not a git checkout")
    offenders = [
        line for line in done.stdout.splitlines()
        if line.strip() and not any(part in line for part in _IGNORABLE_UNDER_PACKAGES)
    ]
    assert not offenders, (
        "gitignored paths under packages/ that are not build output:\n  "
        + "\n  ".join(offenders)
        + "\nThese will never be committed and never ship in a wheel. Rename them "
          "out of the ignored path rather than using `git add -f`."
    )


def test_every_package_is_at_the_suite_version():
    for path in PYPROJECTS:
        project = tomllib.loads(path.read_text())["project"]
        assert project["version"] == VERSION, f"{path.parent.name} is at {project['version']}"


def test_sibling_pins_are_exact_and_lockstep():
    for path in PYPROJECTS:
        project = tomllib.loads(path.read_text())["project"]
        for requirement in project.get("dependencies", []):
            if requirement.startswith("toto-"):
                assert requirement.endswith(f"=={VERSION}"), f"{path.parent.name}: {requirement}"


def test_release_script_agrees():
    done = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "release.py"), "--check"],
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_package_graph_holds():
    done = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_package_graph.py")],
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr


# --- manifest parsing -----------------------------------------------------


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "requirements.toto.txt"
    path.write_text(text)
    return path


def test_manifest_reads_exact_pins(tmp_path):
    manifest = read_manifest(write(tmp_path, "# comment\ntoto-base==1.4\ntoto-flow==1.4\n\n"))
    assert manifest.pins == {"toto-base": "1.4", "toto-flow": "1.4"}
    assert manifest.version == "1.4"


@pytest.mark.parametrize("line", [
    "toto-base @ git+https://example.com/toto_libs.git@v1.4",   # no git host in a manifest
    "toto-base>=1.4",                                           # not exact
    "toto-base==1.4.2",                                         # not MAJOR.RELEASE
    "django==4.2",                                              # not a toto package
])
def test_manifest_rejects_anything_but_exact_toto_pins(tmp_path, line):
    with pytest.raises(TotoVersionError):
        read_manifest(write(tmp_path, line + "\n"))


def test_manifest_rejects_mixed_versions(tmp_path):
    with pytest.raises(TotoVersionError, match="lockstep|mixes versions"):
        read_manifest(write(tmp_path, "toto-base==1.4\ntoto-flow==1.5\n"))


def test_manifest_must_exist(tmp_path):
    with pytest.raises(TotoVersionError):
        read_manifest(tmp_path / "absent.txt")


# --- wheel verification ---------------------------------------------------


def test_parse_wheel_normalizes_the_underscore_spelling():
    assert parse_wheel("dist/toto_base-1.4-py3-none-any.whl") == ("toto-base", "1.4")


def test_verify_wheels_accepts_the_exact_set(tmp_path):
    manifest = read_manifest(write(tmp_path, "toto-base==1.4\ntoto-flow==1.4\n"))
    verify_wheels(["toto_base-1.4-py3-none-any.whl", "toto_flow-1.4-py3-none-any.whl"], manifest)


def test_verify_wheels_rejects_a_wrong_version(tmp_path):
    manifest = read_manifest(write(tmp_path, "toto-base==1.4\n"))
    with pytest.raises(TotoVersionError, match="wrong version"):
        verify_wheels(["toto_base-1.5-py3-none-any.whl"], manifest)


def test_verify_wheels_rejects_a_missing_package(tmp_path):
    manifest = read_manifest(write(tmp_path, "toto-base==1.4\ntoto-flow==1.4\n"))
    with pytest.raises(TotoVersionError, match="missing wheels"):
        verify_wheels(["toto_base-1.4-py3-none-any.whl"], manifest)


def test_verify_wheels_rejects_a_stale_extra_wheel(tmp_path):
    manifest = read_manifest(write(tmp_path, "toto-base==1.4\n"))
    with pytest.raises(TotoVersionError, match="unexpected"):
        verify_wheels(["toto_base-1.4-py3-none-any.whl", "toto_graph-1.4-py3-none-any.whl"], manifest)


# --- checkout verification ------------------------------------------------


def fake_checkout(tmp_path: Path, version: str, packages=("toto-base",)) -> Path:
    src = tmp_path / "toto_libs"
    (src / "packages").mkdir(parents=True)
    (src / "VERSION").write_text(version + "\n")
    for name in packages:
        pkg = src / "packages" / name
        pkg.mkdir()
        (pkg / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "{version}"\n')
    return src


def test_verify_checkout_accepts_a_matching_tree(tmp_path):
    manifest = read_manifest(write(tmp_path, "toto-base==1.4\n"))
    assert verify_checkout(fake_checkout(tmp_path, "1.4"), manifest) == "1.4"


def test_verify_checkout_rejects_a_checkout_ahead_of_the_pin(tmp_path):
    """The everyday failure: hacking on the library, deploying by muscle memory."""
    manifest = read_manifest(write(tmp_path, "toto-base==1.4\n"))
    with pytest.raises(TotoVersionError, match="is at version 1.5") as caught:
        verify_checkout(fake_checkout(tmp_path, "1.5"), manifest)
    assert "checkout v1.4" in caught.value.remedy


def test_verify_checkout_rejects_a_pre_split_checkout(tmp_path):
    manifest = read_manifest(write(tmp_path, "toto-base==1.4\n"))
    legacy = tmp_path / "old_libs"
    (legacy / "toto").mkdir(parents=True)
    with pytest.raises(TotoVersionError, match="pre-split"):
        verify_checkout(legacy, manifest)


def test_verify_checkout_rejects_a_missing_package(tmp_path):
    manifest = read_manifest(write(tmp_path, "toto-base==1.4\ntoto-graph==1.4\n"))
    with pytest.raises(TotoVersionError, match="toto-graph"):
        verify_checkout(fake_checkout(tmp_path, "1.4", packages=("toto-base",)), manifest)


def test_dev_mode_builds_whatever_the_checkout_holds(tmp_path):
    """--dev exists to try unreleased library code in a host, so it must not
    insist on the pinned version — it reports what it will actually build."""
    manifest = read_manifest(write(tmp_path, "toto-base==1.4\n"))
    assert verify_checkout(fake_checkout(tmp_path, "1.5"), manifest, strict=False) == "1.5"


def test_dev_mode_still_requires_every_pinned_package(tmp_path):
    manifest = read_manifest(write(tmp_path, "toto-base==1.4\ntoto-graph==1.4\n"))
    with pytest.raises(TotoVersionError, match="toto-graph"):
        verify_checkout(fake_checkout(tmp_path, "1.5", packages=("toto-base",)), manifest, strict=False)


def test_dev_wheels_may_differ_from_the_pin_but_not_from_each_other(tmp_path):
    manifest = read_manifest(write(tmp_path, "toto-base==1.4\ntoto-flow==1.4\n"))
    verify_wheels(
        ["toto_base-1.5-py3-none-any.whl", "toto_flow-1.5-py3-none-any.whl"],
        manifest, exact_version=False,
    )
    with pytest.raises(TotoVersionError, match="mix versions"):
        verify_wheels(
            ["toto_base-1.5-py3-none-any.whl", "toto_flow-1.4-py3-none-any.whl"],
            manifest, exact_version=False,
        )


# --- runtime coherence ----------------------------------------------------


def test_runtime_coherence_rejects_the_legacy_distribution(monkeypatch):
    from toto import versioning

    monkeypatch.delenv(versioning.SKIP_ENV, raising=False)
    monkeypatch.setattr(versioning, "installed_suite", lambda: {"toto": "0.3.2", "toto-base": "1.4"})
    with pytest.raises(TotoVersionError, match="pre-split"):
        versioning.check_runtime_coherence()


def test_runtime_coherence_rejects_mixed_versions(monkeypatch):
    from toto import versioning

    monkeypatch.delenv(versioning.SKIP_ENV, raising=False)
    monkeypatch.setattr(versioning, "installed_suite", lambda: {"toto-base": "1.4", "toto-graph": "1.5"})
    with pytest.raises(TotoVersionError, match="incoherent"):
        versioning.check_runtime_coherence()


def test_runtime_coherence_accepts_a_coherent_suite(monkeypatch):
    from toto import versioning

    monkeypatch.delenv(versioning.SKIP_ENV, raising=False)
    monkeypatch.setattr(versioning, "installed_suite", lambda: {"toto-base": "1.4", "toto-graph": "1.4"})
    versioning.check_runtime_coherence()


def test_runtime_coherence_can_be_skipped_for_local_work(monkeypatch):
    from toto import versioning

    monkeypatch.setenv(versioning.SKIP_ENV, "1")
    monkeypatch.setattr(versioning, "installed_suite", lambda: {"toto-base": "1.4", "toto-graph": "1.5"})
    versioning.check_runtime_coherence()
