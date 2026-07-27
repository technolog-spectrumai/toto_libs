#!/usr/bin/env python3
"""Verify the toto package partition: membership, DAG, hard edges, versions.

Membership comes from the filesystem — which ``packages/<dist>/src/toto/<portion>``
a module lives in — and the dependency DAG from each package's ``[project]
dependencies``. Neither is duplicated in a mapping file, so neither can drift.

A *hard* edge is one that runs at import time or is enforced by the database:
module-level imports, ``ForeignKey("app.Model")`` strings, migration
dependencies, and the ``ready()`` guards listed in ``EXTRA_EDGES``. Imports
inside functions, under ``if TYPE_CHECKING``, or inside ``try/except
ImportError`` are *soft* (optional integrations) and are ignored on purpose —
they must never become install dependencies.

Exit code 0 = the partition holds.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent

# Requirements that no static scan can see: apps whose AppConfig.ready() raises
# ImproperlyConfigured unless another app is installed.
EXTRA_EDGES: tuple[tuple[str, str, str], ...] = (
    ("bento", "ravioli", "bento/apps.py ready() guard"),
    ("ingestor", "ravioli", "ingestor/apps.py ready() guard"),
    ("ingestor", "bento", "ingestor/apps.py ready() guard"),
    ("connectors", "ravioli", "connectors/apps.py ready() guard"),
    ("connectors", "bento", "connectors/apps.py ready() guard"),
    ("connectors", "ingestor", "connectors/apps.py ready() guard"),
    ("connectors", "api", "connectors/apps.py ready() guard"),
)

# Django app labels that are not toto apps (never produce an edge).
EXTERNAL_LABELS = frozenset({
    "auth", "admin", "contenttypes", "sessions", "sites", "messages",
    "staticfiles", "gis", "postgres", "sitemaps", "flatpages", "humanize",
})

RELATION_FIELDS = frozenset({"ForeignKey", "OneToOneField", "ManyToManyField"})
MODEL_REF_RE = re.compile(r"^([a-z_][a-z0-9_]*)\.[A-Z]")


def is_test_file(path: Path) -> bool:
    """Tests may import across packages freely: the dev env installs everything."""
    if path.name == "testutils.py":
        return False
    if "tests" in path.parts:
        return True
    return path.name == "tests.py" or path.name.startswith(("test_", "tests_"))


def is_extension_point(path: Path) -> bool:
    """Modules loaded only by another app's autodiscovery, never on import.

    ``toto.core.plugin_autodiscover.autodiscover_plugins("plugins.<name>")``
    walks INSTALLED_APPS and imports ``<app>.plugins.<name>``; workflows does
    the same for ``<app>.predefined_tasks``. Both swallow ModuleNotFoundError,
    so these files run only when the *discovering* app is installed — their
    imports are optional integrations, not install dependencies.
    """
    if path.name == "predefined_tasks.py":
        return True
    return (
        path.parent.name == "plugins"
        and path.name.endswith("_plugins.py")
        and path.name != "__init__.py"
    )


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------


def discover_split(packages_dir: Path) -> tuple[dict[str, str], dict[str, tuple[str, ...]], dict[str, str], list[str]]:
    """Return (module -> dist, dist -> sibling deps, dist -> version, errors)."""
    owner: dict[str, str] = {}
    deps: dict[str, tuple[str, ...]] = {}
    versions: dict[str, str] = {}
    errors: list[str] = []

    for pkg_dir in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        pyproject = pkg_dir / "pyproject.toml"
        if not pyproject.is_file():
            errors.append(f"{pkg_dir}: no pyproject.toml")
            continue
        meta = tomllib.loads(pyproject.read_text())["project"]
        dist = meta["name"]
        versions[dist] = meta.get("version", "")
        deps[dist] = tuple(
            re.split(r"[=<>!~ ]", req, maxsplit=1)[0]
            for req in meta.get("dependencies", [])
            if req.startswith("toto-")
        )
        src = pkg_dir / "src" / "toto"
        if not src.is_dir():
            errors.append(f"{dist}: missing src/toto/")
            continue
        if (src / "__init__.py").exists():
            errors.append(f"{dist}: src/toto/__init__.py breaks the PEP 420 namespace")
        for entry in sorted(src.iterdir()):
            if entry.name == "__pycache__":
                continue
            name = entry.stem if entry.suffix == ".py" else entry.name
            if entry.is_dir() or entry.suffix == ".py":
                if name in owner:
                    errors.append(f"'{name}' claimed by both {owner[name]} and {dist}")
                owner[name] = dist
    return owner, deps, versions, errors


def source_roots(root: Path) -> list[Path]:
    return sorted((root / "packages").glob("*/src/toto"))


def module_of(path: Path, roots: list[Path]) -> str | None:
    """Top-level toto member owning this file, e.g. toto/vault/views.py -> 'vault'."""
    for src in roots:
        try:
            rel = path.relative_to(src)
        except ValueError:
            continue
        return rel.parts[0][:-3] if len(rel.parts) == 1 and rel.parts[0].endswith(".py") else rel.parts[0]
    return None


# --------------------------------------------------------------------------
# hard-edge scanning
# --------------------------------------------------------------------------


class HardImportVisitor(ast.NodeVisitor):
    """Collect ``toto.<member>`` imports that execute at import time."""

    def __init__(self) -> None:
        self.hits: list[tuple[str, int]] = []
        self._soft_depth = 0

    # imports inside a function body are lazy by construction
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._soft_depth += 1
        self.generic_visit(node)
        self._soft_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Try(self, node: ast.Try) -> None:
        guarded = any(
            _handler_catches_import_error(h) for h in node.handlers
        )
        if guarded:
            self._soft_depth += 1
            for child in node.body:
                self.visit(child)
            self._soft_depth -= 1
            for child in node.handlers + node.orelse + node.finalbody:
                self.visit(child)
        else:
            self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            self._soft_depth += 1
            for child in node.body:
                self.visit(child)
            self._soft_depth -= 1
            for child in node.orelse:
                self.visit(child)
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if not self._soft_depth:
            for alias in node.names:
                member = _toto_member(alias.name)
                if member:
                    self.hits.append((member, node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if not self._soft_depth and node.module and node.level == 0:
            member = _toto_member(node.module)
            if member:
                self.hits.append((member, node.lineno))


def _handler_catches_import_error(handler: ast.ExceptHandler) -> bool:
    names: list[str] = []
    exc = handler.type
    if exc is None:
        return True
    for node in ast.walk(exc):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
    return any(n in {"ImportError", "ModuleNotFoundError", "Exception"} for n in names)


def _is_type_checking(test: ast.expr) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id == "TYPE_CHECKING")
        or (isinstance(n, ast.Attribute) and n.attr == "TYPE_CHECKING")
        for n in ast.walk(test)
    )


def _toto_member(dotted: str) -> str | None:
    parts = dotted.split(".")
    return parts[1] if len(parts) >= 2 and parts[0] == "toto" else None


def scan_model_refs(tree: ast.AST) -> list[tuple[str, int]]:
    """``ForeignKey("app.Model")`` / M2M / O2O string targets."""
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in RELATION_FIELDS:
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords if kw.arg == "to"]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                m = MODEL_REF_RE.match(arg.value)
                if m and m.group(1) not in EXTERNAL_LABELS:
                    hits.append((m.group(1), node.lineno))
    return hits


def scan_migration_deps(tree: ast.AST) -> list[tuple[str, int]]:
    """``dependencies = [("app", "0001_initial")]`` inside a Migration class."""
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "dependencies" for t in node.targets):
            continue
        for elt in ast.walk(node.value):
            if isinstance(elt, ast.Tuple) and elt.elts:
                first = elt.elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    label = first.value
                    if label and label not in EXTERNAL_LABELS and label.islower():
                        hits.append((label, node.lineno))
    return hits


def collect_hard_edges(roots: list[Path], owner: dict[str, str]) -> list[tuple[str, str, str]]:
    """Return (src_member, dst_member, 'file:line reason') for every hard edge."""
    edges: list[tuple[str, str, str]] = []
    for src_root in roots:
        for path in sorted(src_root.rglob("*.py")):
            if "__pycache__" in path.parts or is_test_file(path) or is_extension_point(path):
                continue
            member = module_of(path, roots)
            if member is None:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError as exc:
                edges.append((member, member, f"{path}: unparseable ({exc})"))
                continue

            visitor = HardImportVisitor()
            visitor.visit(tree)
            rel = path.relative_to(REPO_ROOT)
            for target, lineno in visitor.hits:
                if target != member and target in owner:
                    edges.append((member, target, f"{rel}:{lineno} module-level import"))

            is_migration = "migrations" in path.parts
            if path.name == "models.py" or is_migration:
                for label, lineno in scan_model_refs(tree):
                    if label != member and label in owner:
                        edges.append((member, label, f"{rel}:{lineno} relation to '{label}'"))
            if is_migration:
                for label, lineno in scan_migration_deps(tree):
                    if label != member and label in owner:
                        edges.append((member, label, f"{rel}:{lineno} migration dependency"))

    for src, dst, why in EXTRA_EDGES:
        edges.append((src, dst, why))
    return edges


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def closure(deps: dict[str, tuple[str, ...]], dist: str) -> set[str]:
    seen: set[str] = set()
    stack = list(deps.get(dist, ()))
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(deps.get(cur, ()))
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    root: Path = args.root.resolve()

    errors: list[str] = []
    versions: dict[str, str] = {}
    packages_dir = root / "packages"

    if not packages_dir.is_dir():
        print(f"ERROR: no packages/ directory under {root}")
        return 1
    owner, deps, versions, errors = discover_split(packages_dir)
    if (root / "toto").is_dir():
        errors.append("legacy top-level toto/ still exists alongside packages/")

    # 1. every declared package must be known to the dep graph
    for dist in sorted(set(owner.values())):
        deps.setdefault(dist, ())
    for dist, dist_deps in deps.items():
        for dep in dist_deps:
            if dep not in deps:
                errors.append(f"{dist} depends on unknown package '{dep}'")

    # 2. the declared dependency graph must be acyclic
    try:
        TopologicalSorter({d: set(v) for d, v in deps.items()}).prepare()
    except CycleError as exc:
        errors.append(f"package dependency cycle: {exc.args[1]}")

    # 3. every hard edge must be intra-package or follow a declared dep path
    roots = source_roots(root)
    violations: dict[tuple[str, str], list[str]] = {}
    for src, dst, why in collect_hard_edges(roots, owner):
        src_dist, dst_dist = owner.get(src), owner.get(dst)
        if not src_dist or not dst_dist or src_dist == dst_dist:
            continue
        if dst_dist not in closure(deps, src_dist):
            violations.setdefault((src_dist, dst_dist), []).append(f"{src} -> {dst}: {why}")

    # 4. version discipline (split mode only)
    version_file = root / "VERSION"
    if version_file.is_file():
        declared = version_file.read_text().strip()
        for dist, version in sorted(versions.items()):
            if version != declared:
                errors.append(f"{dist} version {version!r} != VERSION {declared!r}")
        for pkg_dir in sorted(packages_dir.iterdir()):
            pyproject = pkg_dir / "pyproject.toml"
            if not pyproject.is_file():
                continue
            meta = tomllib.loads(pyproject.read_text())["project"]
            for req in meta.get("dependencies", []):
                if req.startswith("toto-") and req.split("==")[-1].strip() != declared:
                    errors.append(f"{meta['name']}: sibling pin {req!r} != VERSION {declared!r}")

    if not args.quiet:
        print(f"packages: {len(deps)}   members: {len(owner)}")
    for (src_dist, dst_dist), reasons in sorted(violations.items()):
        print(f"\nUNDECLARED DEPENDENCY {src_dist} -> {dst_dist} ({len(reasons)} hard edges):")
        for reason in reasons[:12]:
            print(f"  {reason}")
        if len(reasons) > 12:
            print(f"  ... and {len(reasons) - 12} more")
    for err in errors:
        print(f"ERROR: {err}")

    if violations or errors:
        print(f"\nFAILED: {len(violations)} undeclared package dependencies, {len(errors)} errors")
        return 1
    if not args.quiet:
        print("OK: partition holds (membership complete, DAG acyclic, no undeclared hard edges)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
