"""The manager half must import with no Django at all.

This is an architectural boundary, not a style preference. The trusted manager
runs in its own container with the Docker socket and **no** DJANGO_SETTINGS_
MODULE, no database and no secret key — that is precisely what makes it safe to
give it the socket. A stray ``from django.conf import settings`` at module scope
in the shared modules would make the manager unstartable, and the failure would
appear at deploy time on a machine nobody is watching.

So the test runs a subprocess with Django uninstalled from its import path and
imports the shared core. Subprocess rather than in-process because Django is
already imported by the time this test runs, and a module cache cannot be
un-imported honestly.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from django.test import SimpleTestCase

#: The modules the manager needs, and therefore the ones that may not touch
#: Django. Everything else in the package is free to.
DJANGO_FREE = (
    # The shared core the executor reads its vocabulary from.
    "toto.anastasia.limits",
    "toto.anastasia.families",
    "toto.anastasia.choices",
    # The executor itself. This is the half that runs as a root systemd unit
    # on the host, with no settings module and no database — and the boundary
    # is load-bearing rather than tidy: it is what keeps SECRET_KEY, the
    # database credentials and the vault key out of the one process that can
    # run other people's code.
    "toto.anastasia.executor",
    "toto.anastasia.executor.protocol",
    "toto.anastasia.executor.staging",
    "toto.anastasia.executor.slices",
    "toto.anastasia.executor.drivers",
    "toto.anastasia.executor.drivers.docker",
    "toto.anastasia.executor.runners",
    "toto.anastasia.executor.gears",
    "toto.anastasia.executor.reconcile",
    "toto.anastasia.executor.pressure",
    "toto.anastasia.executor.control",
    "toto.anastasia.executor.telemetry",
    "toto.anastasia.executor.service",
    "toto.anastasia.executor.__main__",
)

PACKAGE_SRC = Path(__file__).resolve().parents[3]


class DjangoFreeCoreTests(SimpleTestCase):
    def test_the_shared_core_imports_without_django(self):
        program = textwrap.dedent(f"""
            import sys

            class NoDjango:
                \"\"\"Make any django import fail the way an absent install would.

                find_spec, not find_module: the legacy finder API was REMOVED in
                Python 3.12, so a find_module-based guard is silently inert on a
                modern interpreter and the test passes for the wrong reason.
                \"\"\"
                def find_spec(self, name, path=None, target=None):
                    if name == "django" or name.startswith("django."):
                        raise ImportError(
                            "django is not installed in the manager image")
                    return None

            sys.meta_path.insert(0, NoDjango())
            sys.path.insert(0, {str(PACKAGE_SRC)!r})

            # Prove the guard bites BEFORE trusting what it lets through —
            # otherwise an inert guard makes every assertion below vacuous.
            try:
                import django
            except ImportError:
                pass
            else:
                print("GUARD-INERT")
                raise SystemExit(2)

            import importlib
            for name in {DJANGO_FREE!r}:
                importlib.import_module(name)
            print("OK")
        """)
        result = subprocess.run([sys.executable, "-c", program],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0,
                         f"stdout={result.stdout}\nstderr={result.stderr}")
        self.assertIn("OK", result.stdout, result.stderr)

    def test_the_core_modules_do_not_import_django_at_module_scope(self):
        """A cheap static double-check, so the failure names the file."""
        import ast

        for dotted in DJANGO_FREE:
            base = PACKAGE_SRC / dotted.replace(".", "/")
            # A package (toto.anastasia.executor) is its __init__.py; a module
            # is <name>.py. Both are in the list, so resolve both.
            path = base.with_suffix(".py")
            if not path.exists():
                path = base / "__init__.py"
            self.assertTrue(path.exists(), f"{dotted} resolves to no file")
            tree = ast.parse(path.read_text())
            for node in tree.body:            # module scope only, by design
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    with self.subTest(module=dotted, imports=name):
                        self.assertFalse(
                            name == "django" or name.startswith("django."),
                            f"{path.name} imports {name} at module scope; the "
                            "manager image has no Django.")
