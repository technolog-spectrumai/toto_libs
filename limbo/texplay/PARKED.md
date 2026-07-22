# toto.texplay — parked 2026-07-22 (toto v1.4)

Compiles a single `.tex` vault file to PDF via an async `TexPlayJob`, and registers the
LaTeX **Play** button on vault files (`plugins/vault_play_plugins.py`).

## Why it was parked

It was dead in every deployment, not merely unused:

- absent from every host's `INSTALLED_APPS` — zenobia's `BUILD_LATEX` block installs only
  `toto.texlab`, and faros ships no tex apps at all;
- absent from `registry.FEATURE_APPS` and `registry.TASK_MODULES`, so its celery tasks were
  never autodiscovered and `ingress_texplay` never ran;
- no URLs mounted by any host, and no code anywhere imported it.

Because it never installed, its `VaultPlayPlugin` never registered — so the LaTeX Play
button it provides has not existed in any running deployment. `texlab/README.md` advertised
that button until this release; the claim is now removed.

The 2026-07-11 "simplify editors" refactor left texplay behind while `texlab` and `gitvault`
kept being developed (last meaningful commit here: `29f66636`, 2026-06-05).

## Reviving it

It is self-contained: it depends on `vault`, `workflows` and `ui`, all of which still ship.
The compile path (`compile.py`) overlaps with `toto.texlab`'s, so decide first whether the
Play button belongs in texlab rather than reviving a second compiler. `texlab` and
`gitvault` are host-owned by zenobia as of v1.4 — see `secession.md`.
