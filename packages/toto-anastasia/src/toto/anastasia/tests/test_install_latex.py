"""`anastasia-install-latex`: resolving, downloading and unpacking a CTAN package.

The runner lives in `deploy/anastasia/runner` and runs inside the latex image,
so these tests import it from the portal tree and drive it with a fake opener
and archives built here. What they pin is what the runner must REFUSE — a
redirect, a hostile archive, a package with no archive — and that nothing is
written when it does. That CTAN and a real mirror answer the way the fake does
was checked by hand on 2026-09-14 and is on test.md's manual list.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

RUNNER_ROOT = Path(__file__).resolve().parents[8] / "deploy" / "anastasia" / "runner"
HAVE_RUNNER = (RUNNER_ROOT / "anastasia_runner" / "install_latex.py").is_file()

MIRROR = "https://ctan.example.org/tex-archive"


def _zip(members: dict, *, symlinks: dict | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in members.items():
            zf.writestr(name, body)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, target)
    return buffer.getvalue()


class FakeOpener:
    """URL -> bytes, an Exception, or absent (a 404)."""

    def __init__(self, routes):
        self.routes = routes
        self.opened = []

    def open(self, url, timeout=None):
        self.opened.append(url)
        value = self.routes.get(url)
        if value is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {},
                                         io.BytesIO(b'{"errors":["Not found"]}'))
        if isinstance(value, Exception):
            raise value
        return io.BytesIO(value)


def _index(install, version="6.10.0"):
    return json.dumps({"id": "x", "install": install,
                       "version": {"number": version, "date": ""}}).encode()


@unittest.skipUnless(HAVE_RUNNER, f"the runner package is not at {RUNNER_ROOT}")
class InstallLatexRunnerTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if str(RUNNER_ROOT) not in sys.path:
            sys.path.insert(0, str(RUNNER_ROOT))
        from anastasia_runner import common, install_latex

        cls.common = common
        cls.mod = install_latex

    def setUp(self):
        base = tempfile.mkdtemp(prefix="anastasia-texmf-")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.texmf = os.path.join(base, "texmf")
        self.outside = os.path.join(base, "outside")
        os.makedirs(self.outside)

    def _written(self):
        found = []
        for folder, _dirs, files in os.walk(self.texmf):
            for name in files:
                found.append(os.path.relpath(os.path.join(folder, name), self.texmf))
        return sorted(found)

    # -- resolving -------------------------------------------------------------

    def test_a_package_resolves_to_its_tds_archive_and_version(self):
        opener = FakeOpener({self.mod.CTAN_INDEX + "tcolorbox":
                             _index("/macros/latex/contrib/tcolorbox.tds.zip")})
        meta = self.mod.resolve(opener, "tcolorbox")
        self.assertEqual(meta, {"install": "/macros/latex/contrib/tcolorbox.tds.zip",
                                "version": "6.10.0"})
        self.assertEqual(self.mod.archive_url(MIRROR + "/", meta["install"]),
                         MIRROR + "/install/macros/latex/contrib/tcolorbox.tds.zip")

    def test_an_unknown_package_is_named(self):
        with self.assertRaises(self.common.RunnerError) as caught:
            self.mod.resolve(FakeOpener({}), "nosuchpackage")
        self.assertIn("no package named 'nosuchpackage'", str(caught.exception))

    def test_a_package_with_no_ready_archive_is_refused_not_guessed(self):
        """biblatex-apa, among others, ships sources only."""
        opener = FakeOpener({self.mod.CTAN_INDEX + "biblatex-apa": _index(None)})
        with self.assertRaises(self.common.RunnerError) as caught:
            self.mod.resolve(opener, "biblatex-apa")
        self.assertIn("no ready-to-install archive", str(caught.exception))

    def test_an_archive_path_the_index_should_not_shape_is_refused(self):
        for hostile in ("/../../etc/x.tds.zip", "https://evil.example/x.tds.zip",
                        "/macros/x.zip", "/macros/./x.tds.zip", "macros/x.tds.zip"):
            with self.subTest(install=hostile):
                opener = FakeOpener({self.mod.CTAN_INDEX + "pgf": _index(hostile)})
                with self.assertRaises(self.common.RunnerError):
                    self.mod.resolve(opener, "pgf")

    def test_a_name_is_checked_again_before_it_becomes_a_url(self):
        opener = FakeOpener({})
        for bad in ("../pgf", "pgf/x", "PGF", "pgf?x=1"):
            with self.subTest(name=bad):
                with self.assertRaises(self.common.RunnerError):
                    self.mod.resolve(opener, bad)
        self.assertEqual(opener.opened, [])

    # -- downloading -----------------------------------------------------------

    def test_every_redirect_is_refused_naming_both_addresses(self):
        """CTAN's own links redirect to a random mirror the proxy would refuse."""
        handler = self.mod._RefuseRedirects()
        request = mock.Mock(full_url=MIRROR + "/install/x.tds.zip")
        with self.assertRaises(self.common.RunnerError) as caught:
            handler.redirect_request(request, None, 307, "Temporary", {},
                                     "https://random-mirror.example/x.tds.zip")
        self.assertIn("random-mirror.example", str(caught.exception))
        self.assertIn("ctan.example.org", str(caught.exception))

    def test_a_download_over_the_limit_is_refused(self):
        opener = FakeOpener({"https://m/x": b"x" * 100})
        with self.assertRaises(self.common.RunnerError) as caught:
            self.mod.fetch(opener, "https://m/x", limit=10)
        self.assertIn("larger than", str(caught.exception))

    def test_an_unreachable_host_asks_about_the_allowlist(self):
        opener = FakeOpener({"https://m/x": urllib.error.URLError("Forbidden")})
        with self.assertRaises(self.common.RunnerError) as caught:
            self.mod.fetch(opener, "https://m/x", limit=10)
        self.assertIn("allowed list", str(caught.exception))

    # -- unpacking -------------------------------------------------------------

    def test_only_tds_members_are_written_and_readable(self):
        count = self.mod.unpack(_zip({
            "tex/latex/tcolorbox/tcolorbox.sty": "% sty",
            "doc/latex/tcolorbox/README.md": "readme",
            "README": "top-level, not a TeX tree",
            "tlpkg/tlpobj/tcolorbox.tlpobj": "metadata",
            "scripts/tcolorbox/run.sh": "#!/bin/sh",
        }), self.texmf)
        self.assertEqual(count, 2)
        self.assertEqual(self._written(), ["doc/latex/tcolorbox/README.md",
                                           "tex/latex/tcolorbox/tcolorbox.sty"])
        mode = os.stat(os.path.join(self.texmf, "tex/latex/tcolorbox/tcolorbox.sty")).st_mode
        self.assertEqual(stat.S_IMODE(mode), 0o644)

    def test_a_hostile_archive_writes_nothing_at_all(self):
        """Judged whole BEFORE the first write: a good member ahead of a bad
        one must not be left behind."""
        cases = {
            "traversal": _zip({"tex/latex/ok.sty": "ok", "tex/../../outside/x": "x"}),
            "absolute": _zip({"tex/latex/ok.sty": "ok", "/etc/passwd": "x"}),
            "backslash": _zip({"tex/latex/ok.sty": "ok", "tex\\latex\\x.sty": "x"}),
            "symlink": _zip({"tex/latex/ok.sty": "ok"},
                            symlinks={"tex/latex/link.sty": "/etc/passwd"}),
            "drive": _zip({"tex/latex/ok.sty": "ok", "C:/x.sty": "x"}),
        }
        for label, data in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(self.common.RunnerError):
                    self.mod.unpack(data, self.texmf)
                self.assertEqual(self._written(), [])
                self.assertEqual(os.listdir(self.outside), [])

    def test_a_compression_bomb_is_refused(self):
        with self.assertRaises(self.common.RunnerError) as caught:
            self.mod.unpack(_zip({"tex/latex/bomb.sty": "0" * (4 * 1024 * 1024)}),
                            self.texmf)
        self.assertIn("bomb", str(caught.exception))
        self.assertEqual(self._written(), [])

    def test_an_archive_past_the_unpacked_budget_is_refused(self):
        with mock.patch.object(self.mod, "MAX_UNPACKED_BYTES", 10):
            with self.assertRaises(self.common.RunnerError):
                self.mod.unpack(_zip({"tex/latex/a.sty": "x" * 11}), self.texmf)
        self.assertEqual(self._written(), [])

    def test_a_download_that_is_not_a_zip_is_a_sentence(self):
        with self.assertRaises(self.common.RunnerError) as caught:
            self.mod.unpack(b"<html>captive portal</html>", self.texmf)
        self.assertIn("not a zip", str(caught.exception))

    # -- the whole run ------------------------------------------------------------

    def _opener(self):
        return FakeOpener({
            self.mod.CTAN_INDEX + "tcolorbox":
                _index("/macros/latex/contrib/tcolorbox.tds.zip"),
            MIRROR + "/install/macros/latex/contrib/tcolorbox.tds.zip":
                _zip({"tex/latex/tcolorbox/tcolorbox.sty": "% sty"}),
            self.mod.CTAN_INDEX + "biblatex-apa": _index(None),
        })

    def test_one_package_failing_names_it_and_stops_nothing_else(self):
        said = []
        result = self.mod.install(["biblatex-apa", "tcolorbox"], texmf=self.texmf,
                                  mirror=MIRROR, opener=self._opener(),
                                  say=said.append)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed"], ["biblatex-apa"])
        self.assertEqual(result["installed"], ["tcolorbox"])
        self.assertIn("tex/latex/tcolorbox/tcolorbox.sty", self._written())
        self.assertTrue(any(line.startswith("ERROR: biblatex-apa:") for line in said))
        self.assertEqual(said, result["log"].split("\n"))

    def test_an_installed_package_is_recorded_where_the_env_tab_reads(self):
        self.mod.install(["tcolorbox"], texmf=self.texmf, mirror=MIRROR,
                         opener=self._opener(), say=lambda line: None)
        with open(os.path.join(self.texmf, self.mod.MARKERS, "tcolorbox")) as handle:
            self.assertEqual(json.load(handle),
                             {"name": "tcolorbox", "version": "6.10.0"})

    def test_the_log_speaks_the_words_the_django_side_counts(self):
        """The runner and `install.py` are two programs in two images; the only
        contract between them is these lines. Asserted from both ends."""
        from toto.anastasia import install

        result = self.mod.install(["tcolorbox", "biblatex-apa"], texmf=self.texmf,
                                  mirror=MIRROR, opener=self._opener(),
                                  say=lambda line: None)
        self.assertEqual(install._reported(result["log"], "latex"), {"tcolorbox"})
        self.assertEqual(install._phase(result["log"]), "installing")

    def test_the_downloads_come_from_the_pinned_mirror_only(self):
        opener = self._opener()
        self.mod.install(["tcolorbox"], texmf=self.texmf, mirror=MIRROR,
                         opener=opener, say=lambda line: None)
        downloads = [url for url in opener.opened
                     if not url.startswith(self.mod.CTAN_INDEX)]
        self.assertEqual(downloads,
                         [MIRROR + "/install/macros/latex/contrib/tcolorbox.tds.zip"])

    def test_the_console_script_is_declared_for_the_manager(self):
        pyproject = (RUNNER_ROOT / "pyproject.toml").read_text()
        self.assertIn('anastasia-install-latex = "anastasia_runner.install_latex:cli"',
                      pyproject)
