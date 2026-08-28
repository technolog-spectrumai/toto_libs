"""The one-way dependency rule inside the Business Center.

The four apps ship as ONE wheel (toto-business), and inside it the arrow only
points up: `company` may import the engines, the engines never import
`company`. The other half of the old rule — that no OTHER app imports a
Business Center app — is the package graph's job now: since the 1.50 move
host → wheel, `scripts/check_package_graph.py` refuses any hard edge into
this package that its pyproject does not declare.
"""

from __future__ import annotations

import ast
from pathlib import Path

from django.test import TestCase
from django.urls import reverse


#: Every Business Center app. Populated as the stages land; the tests below
#: skip a name that does not exist yet rather than failing on it.
BUSINESS_CENTER_APPS = ("company", "ledger", "documents", "voting")

#: What a Business Center app may never import. `toto.portfolio`,
#: `toto.governance` and `toto.irene` are the retired predecessors — importing
#: one would mean the revival reached for dead code.
FORBIDDEN_INSIDE = ("toto.portfolio", "toto.governance", "toto.irene", "toto.decisions")


def _toto_root() -> Path:
    """The `toto/` namespace dir holding the four apps — `src/toto` in the
    wheel's source tree, wherever the wheel is checked out."""
    return Path(__file__).resolve().parents[2]


def _imported_modules(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            # A relative import cannot name another app, so it is never a
            # boundary violation and `node.module` is misleading for it.
            if node.level == 0 and node.module:
                yield node.module


def _python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "migrations" in path.parts or "tests" in path.parts:
            continue
        yield path


def _matches(name: str, target: str) -> bool:
    return name == target or name.startswith(f"{target}.")


class BusinessCenterImportsNothingForbidden(TestCase):
    def test_no_business_center_app_imports_retired_code(self):
        root = _toto_root()
        offenders = []
        for app in BUSINESS_CENTER_APPS:
            base = root / app
            if not base.is_dir():
                continue
            for path in _python_files(base):
                for name in _imported_modules(path):
                    if any(_matches(name, bad) for bad in FORBIDDEN_INSIDE):
                        offenders.append(f"{path.relative_to(root)} imports {name}")
        self.assertEqual(offenders, [])

    def test_the_engines_do_not_import_company(self):
        """ledger, documents and voting must stay Company-blind.

        This is the rule that keeps them reusable, and it is the one the
        revived irena code actually broke — its ledger service imported
        `toto.company.models.Company` directly.
        """
        root = _toto_root()
        offenders = []
        for app in ("ledger", "documents", "voting"):
            base = root / app
            if not base.is_dir():
                continue
            for path in _python_files(base):
                for name in _imported_modules(path):
                    if _matches(name, "toto.company"):
                        offenders.append(f"{path.relative_to(root)} imports {name}")
        self.assertEqual(offenders, [])
