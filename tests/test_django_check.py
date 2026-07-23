"""Prove the INSTALLED wheel (not this checkout) satisfies Django imports.

Runs from a temp cwd with only that dir on PYTHONPATH, so the repository's
toto/ source tree cannot shadow site-packages.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


def _clean_env(tmp_path):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("BUILD_", "INSTALL_", "SABBIA_", "DJANGO_", "PYTHON"))
    }
    env["PYTHONPATH"] = str(tmp_path)
    return env


def test_toto_resolves_from_site_packages(tmp_path):
    # toto is a PEP 420 namespace shared by several distributions: it has no
    # __file__, only __path__. A non-None __file__ means a pre-split 'toto'
    # wheel is installed and shadowing the suite.
    result = subprocess.run(
        [sys.executable, "-c", "import toto; print(toto.__file__); print(*toto.__path__, sep='\\n')"],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    first, *paths = result.stdout.splitlines()
    assert first == "None", f"toto.__file__ is {first} — a legacy 'toto' distribution is installed"
    assert paths and all("site-packages" in p for p in paths), result.stdout


def test_installed_suite_is_coherent(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c",
         "from toto.versioning import check_runtime_coherence, installed_suite;"
         " check_runtime_coherence(); print(sorted(installed_suite().items()))"],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_django_check_passes_from_wheel(tmp_path):
    shutil.copy(TESTS_DIR / "settings_min.py", tmp_path / "settings_min.py")
    shutil.copy(TESTS_DIR / "urls_min.py", tmp_path / "urls_min.py")
    result = subprocess.run(
        [sys.executable, "-m", "django", "check", "--settings=settings_min"],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


# ── BUILD_GEO=0 (GIS-off) proofs ────────────────────────────────────────────
#
# A GIS-off host installs the same wheels but sets HAS_GIS=False. locations must
# then load with NO django.contrib.gis import anywhere, migrate its geometry-less
# graph on plain sqlite, and keep every cross-app FK (socialhub→territory, …)
# resolvable. GDAL-absence is enforced deterministically by blocking the
# contrib.gis import in a subprocess, so the proof holds even where GDAL happens
# to be installed.

# Poisons sys.modules so any `import django.contrib.gis[...]` raises — a stand-in
# for a host that has no GDAL/GEOS at all.
_BLOCK_GIS = "import sys; sys.modules['django.contrib.gis'] = None\n"


def _copy_nogis(tmp_path):
    shutil.copy(TESTS_DIR / "settings_min_nogis.py", tmp_path / "settings_min_nogis.py")
    shutil.copy(TESTS_DIR / "urls_min.py", tmp_path / "urls_min.py")


def test_gis_off_check_passes_without_contrib_gis(tmp_path):
    _copy_nogis(tmp_path)
    prog = _BLOCK_GIS + (
        "from django.core.management import execute_from_command_line;"
        "execute_from_command_line(['django', 'check', '--settings=settings_min_nogis'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", prog],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_gis_off_migrate_builds_geometryless_tables(tmp_path):
    _copy_nogis(tmp_path)
    prog = _BLOCK_GIS + (
        "from django.core.management import execute_from_command_line;"
        "execute_from_command_line(['django', 'migrate', '--noinput', '--settings=settings_min_nogis'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", prog],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    # Introspect the built DB: geometry-model tables exist as geometry-less
    # stubs; Address carries lat/lon; the socialhub→territory FK column is present.
    import sqlite3

    con = sqlite3.connect(tmp_path / "check_nogis.sqlite3")
    cols = lambda t: {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
    try:
        addr = cols("locations_address")
        assert {"latitude", "longitude"} <= addr, addr
        assert "geometry" not in addr, addr
        terr = cols("locations_territory")
        assert terr and "geometry" not in terr, terr
        assert "capital_id" in terr, terr
        for table in ("locations_zone", "locations_route", "locations_maplayerpolygon"):
            assert "geometry" not in cols(table), table
        # The FK edge that must survive on faros (socialhub ships there).
        assert "territory_id" in cols("socialhub_community"), cols("socialhub_community")
    finally:
        con.close()


def test_gis_off_locations_urls_reverse_but_views_404(tmp_path):
    # On a GIS-off host that still mounts toto.locations.urls (e.g. a geo-off
    # zenobia), every locations URL name must stay reversible — so cross-app
    # {% url 'locations:...' %} links (kanban, the manual, the dashboard card)
    # don't NoReverseMatch — while the geometry-driven views degrade to 404
    # instead of raising an AttributeError/FieldError 500.
    prog = _BLOCK_GIS + (
        "import sys, types, django\n"
        "from django.conf import settings\n"
        "from toto.registry import BASE_APPS\n"
        "settings.configure(HAS_GIS=False, DEBUG=True, SECRET_KEY='x',\n"
        "  DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},\n"
        "  INSTALLED_APPS=['django.contrib.contenttypes','django.contrib.auth','django.contrib.admin',\n"
        "    'django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles',\n"
        "    'corsheaders','django_jsonform','django_json_widget','rest_framework','colorfield',\n"
        "    'reversion','markdownx','trix_editor', *BASE_APPS],\n"
        "  ROOT_URLCONF='rc', DEFAULT_AUTO_FIELD='django.db.models.AutoField', USE_TZ=True, STATIC_URL='/s/')\n"
        "rc = types.ModuleType('rc'); sys.modules['rc'] = rc\n"
        "django.setup()\n"
        "from django.urls import path, include, reverse, resolve\n"
        "from django.http import Http404\n"
        "rc.urlpatterns = [path('locations/', include('toto.locations.urls', namespace='locations'))]\n"
        "assert reverse('locations:locations_all') == '/locations/'\n"
        "assert reverse('locations:address_detail', args=[7]) == '/locations/addresses/7/'\n"
        "for p in ['/locations/', '/locations/route-search/', '/locations/api/addresses/1/']:\n"
        "    try:\n"
        "        resolve(p).func(None); raise SystemExit('FAIL: %s did not 404' % p)\n"
        "    except Http404:\n"
        "        pass\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", prog],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_locations_migrations_match_models_in_both_modes(tmp_path):
    # The two migration graphs are hand-maintained in lockstep; makemigrations
    # --check proves each matches its model set (no drift), scoped to locations
    # so unrelated apps can't fail this.
    shutil.copy(TESTS_DIR / "settings_min.py", tmp_path / "settings_min.py")
    _copy_nogis(tmp_path)
    for settings_mod in ("settings_min", "settings_min_nogis"):
        result = subprocess.run(
            [sys.executable, "-m", "django", "makemigrations", "--check", "--dry-run",
             "locations", f"--settings={settings_mod}"],
            cwd=tmp_path,
            env=_clean_env(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"[{settings_mod}] locations models drifted from migrations\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
