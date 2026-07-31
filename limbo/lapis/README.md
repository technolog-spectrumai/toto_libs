# limbo/lapis — the Lapis smart-contract layer + Obligation (parked)

Pulled out of the live `toto.assets` app on **2026-07-31**. The ledger core stays
live in `toto.assets` (Asset, LedgerAccount, holdings, ledger transactions/entries,
the SHA-256 hash chain, Currency, the wallet + wallet PIN). This layer sat on top of
it and was not mature — the app's own README header even said "no smart contracts" —
so it was moved here rather than deleted.

## What is here

| Path | What it is |
|---|---|
| `vm/` | The **Lapis VM** — a YAML smart-contract loader/compiler/executor (`loader`, `compiler`, `executor`, `nodes`, `exceptions`, `cytoscape`). Moved verbatim from `assets/lapis/`. |
| `models.py` | The three models removed from `toto.assets`: **Obligation** (a recorded debt), **Contract** (a stored Lapis contract), **Agreement** (a bilateral agreement governed by a Contract). |
| `services.py` | `create_obligation` / `fulfill_obligation`, removed from `assets.services.assets`. |
| `forms.py` | `AgreementForm` / `ContractForm` (the Lapis YAML editors). Moved verbatim. |
| `templates/assets/` | `agreement_*` and `contract_*` templates. Moved verbatim. |

These depend on the ledger core that remains in `toto.assets`. This is **parked**: not
in `INSTALLED_APPS`, not tested, and — because `limbo/` lives outside `packages/` — never
shipped in any wheel (a packaging test asserts that).

## The thin Django glue that was NOT copied here

The admin registrations (`ContractAdmin`, `AgreementAdmin`, `ObligationAdmin`), the URL
routes, the view functions (`obligation_fulfill`, `agreement_*`, `contract_*`), and the
API views (`ObligationsApiView`, `ObligationFulfillApiView`), plus the ingress seed
methods (`_seed_contracts`, `_seed_sample_agreements`, `_seed_founder_obligations`), were
boilerplate wrappers over the models above. They were removed from the live app and are
recoverable from git history at the commit that removed them; the substantive code —
the VM, the models, the services, the forms and templates — is preserved here.

## Reviving

`Obligation`/`Contract`/`Agreement` are referenced by several other parked economy apps
(`payroll`, `loans`, `insurance`, `magistrate`, `tribunal`, `bazaar` — see
`../economy.md`). Reintroduce them by copying these models back into `toto.assets`
(re-adding the tables in a migration), restoring the glue from git, and installing the
VM as `assets/lapis/` again.
