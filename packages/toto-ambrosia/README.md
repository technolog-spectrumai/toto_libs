# toto-ambrosia

The shared **workspace** base: a folder with a file tree, and the settings,
hibernation and Compute-Capsule-selection machinery over it.

It carries no language of its own. The language apps — `toto.texlab` (LaTeX)
and `toto.dracena` (Python) — are host portions that embed
ambrosia's urlpatterns under their own prefix and register into its registry
from their own `ready()`. Ambrosia never imports them.

## Why it is a wheel

It became one on 2026-09-01, for the same reason `toto-anastasia` is one: a
workspace is not zenobia-specific, and **two hosts need it at once**.

- `placidia` needs it under the labs.
- `zenobia` needs it under version control: `REPO_WORKSPACES_ONLY = True` makes
  `repo.services.create_repo` refuse any directory that is not a workspace
  root, so a zenobia without ambrosia has a git feature that can create zero
  repositories.

A PEP 420 host portion belongs to exactly one host, so the split forced the
promotion. Before that it lived at `zenobia/zenobia/toto/ambrosia`.

## What it depends on

`toto-base` only. Its references to `toto.anastasia`, `toto.dracena`,
`toto.repo` and `toto.texlab` are **all soft** — `apps.is_installed` plus
function-scoped imports — so a host may install ambrosia with none of them.

`capsules.py` is the single module that knows Compute Capsules exist; the
base's `settings_spec` carries only a generic `choices_for`. It was `gears.py`
until 2026-09-10.

## A note on its tests

`tests/test_capsules.py`, `tests/test_hibernation.py`, `tests/test_settings.py`
and `tests/test_views.py` import `toto.anastasia` and `toto.dracena` at module
scope. They are therefore named by the gate of the host that installs those
apps, not run from this package in isolation.

**They also reach into dracena for their fixtures**, which has cost twice:
the clamp tests in `test_settings.py` were written against the
`dracena.kernel_idle` time dial and would have gone vacuous rather than red
when that dial was deleted (`times.free_seconds` answers 0 for an unknown key
rather than raising), and the hybrid hibernation tests drove dracena's Jupyter
kernel. Both now declare their own fixtures — a `TimeLimit` registered per test
and a fake lab in the registry — so retiring an app cannot silently disarm
them.
