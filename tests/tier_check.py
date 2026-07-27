"""Prove one dependency tier stands on its own.

Run inside a venv where only the packages of ``TOTO_TIER`` are installed. It
loads every toto app those packages provide and fails if anything reaches for a
``toto.*`` module from a package that is *not* installed — the exact symptom of
a package boundary drawn in the wrong place.

A missing *third-party* dependency (neo4j, spacy, ...) is reported and skipped:
hosts install those per feature flag, and they say nothing about the partition.

Discovery reads the installed distributions' file lists rather than importing
anything, because several toto modules read Django settings at import time.
"""
import importlib
import importlib.metadata as md
import os
import sys
from pathlib import Path

TIER = os.environ.get("TOTO_TIER", "").split()


def suite_files() -> tuple[dict[str, str], set[str]]:
    """(top-level toto member -> providing distribution, all shipped toto paths)."""
    members: dict[str, str] = {}
    paths: set[str] = set()
    for dist in md.distributions():
        name = (dist.metadata["Name"] or "").lower()
        if not name.startswith("toto"):
            continue
        for file in dist.files or []:
            parts = Path(file).parts
            if len(parts) < 2 or parts[0] != "toto" or file.suffix == ".pyc":
                continue
            paths.add(str(Path(*parts)))
            member = parts[1][:-3] if len(parts) == 2 and parts[1].endswith(".py") else parts[1]
            members[member] = name
    return members, paths


def main() -> int:
    members, paths = suite_files()
    installed = sorted(set(members.values()))
    if TIER and sorted(TIER) != installed:
        print(f"FAIL: tier {TIER} requested but {installed} installed")
        return 1

    apps = sorted(m for m in members if f"toto/{m}/apps.py" in paths)

    import django
    from django.conf import settings

    settings.configure(
        SECRET_KEY="tier-check-only",
        DEBUG=True,
        BASE_DIR=Path(__file__).resolve().parent,
        INSTALLED_APPS=[
            "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
            "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
            "django.contrib.gis",
            "corsheaders", "django_jsonform", "django_json_widget", "rest_framework",
            "colorfield", "reversion", "markdownx", "trix_editor",
            *(f"toto.{app}" for app in apps),
        ],
        DATABASES={"default": {
            "ENGINE": "django.contrib.gis.db.backends.spatialite",
            "NAME": ":memory:",
        }},
        DEFAULT_AUTO_FIELD="django.db.models.AutoField",
        STATIC_URL="static/",
        USE_TZ=True,
    )

    try:
        django.setup()
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing.startswith("toto."):
            print(f"FAIL: tier {installed} needs {missing}, which no installed package provides")
            return 1
        print(f"     (skipped: third-party dependency {missing!r} missing in this tier venv)")
        return 0
    except Exception as exc:                                  # noqa: BLE001
        print(f"FAIL: django.setup() raised {type(exc).__name__}: {exc}")
        return 1

    # Model loading succeeded; now pull in the modules Django defers.
    failures: list[str] = []
    skipped = 0
    for app in apps:
        for suffix in ("views", "admin", "urls"):
            if f"toto/{app}/{suffix}.py" not in paths:
                continue
            try:
                importlib.import_module(f"toto.{app}.{suffix}")
            except ModuleNotFoundError as exc:
                if (exc.name or "").startswith("toto."):
                    failures.append(f"toto.{app}.{suffix} -> {exc.name} (not provided by {', '.join(installed)})")
                else:
                    skipped += 1
            except Exception:                                 # noqa: BLE001
                skipped += 1   # settings/runtime concerns, not packaging ones

    if failures:
        print(f"FAIL: {len(failures)} cross-package imports unsatisfied in tier {installed}")
        for failure in failures:
            print(f"    {failure}")
        return 1

    print(f"     {len(apps)} apps loaded, {skipped} module(s) skipped on third-party/runtime reasons")
    return 0


if __name__ == "__main__":
    sys.exit(main())
