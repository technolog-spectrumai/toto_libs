# toto.ledger — the chain

Linear, append-only, independently verifiable. Blocks, canonical XML, hashes,
signatures, verification and exports — and **nothing about companies**.

## The boundary is the point

This app takes a `scope_type` and a `scope_uid` and never looks them up. It has
no import of `toto.company` and no reverse relation into it; `tests/` and
`company/tests/test_boundary.py` both enforce that. `toto.company` binds a chain
to a Company in `company/integration/ledger.py`, which is the only module in the
tree that knows `"company.company"` means anything.

That is not tidiness. irena's version of this engine imported
`toto.company.models.Company` directly and carried a `company` foreign key,
which is exactly why it could not be reused.

## Three rules the code enforces

**The bytes are the truth.** A block's payload is frozen as canonical XML text
and that text is hashed — not a dict that happens to serialize the same way
today. Serialization is defined in `canonical.py`, versioned by
`FORMAT_VERSION`, and never left to a library's defaults.

**The preimage is injective.** Every value carries its type as its tag, every
container delimits its members, and map keys are sorted. `True` and `1` are
different preimages; so are `1` and `"1"`; so are `{"a": "xy", "b": "z"}` and
`{"a": "x", "b": "yz"}`. That last pair is the boundary-sliding collision
netstrings exist to prevent, prevented here by tags instead.

**The algorithm is a name, not an import.** Nothing calls `hashlib.sha256`
directly. The genesis block records `hash_algorithm` and `format_version`;
`chain_algorithm()` reads the genesis copy, and `resolve()` turns the recorded
name into a function or refuses. SHA-256 is the default because it is the
interoperable choice, but a chain sealed with anything in `ALGORITHMS` stays
verifiable forever.

`Ledger.algorithm` is a convenience column no verifier trusts — repointing it
does not move the chain, and there is a test that says so.

## Why the hash is over the canonical form

`entry_block_xml()` runs its constructed text through `canonicalize()` before
returning. Without that, verifying an export would demand that an auditor
reproduce this codebase's exact spelling — and `<previous-hash></previous-hash>`
versus `<previous-hash/>` is enough to make an honest verifier report a forgery.
`ElementTree` writes the second. So the rule is: parse however you like,
canonicalize, hash. `test_export.py` performs exactly that procedure with
nothing but the stdlib.

## Immutability has two layers, and they do different jobs

`LedgerEntry.save()` and `.delete()` raise. That covers the ORM and produces a
readable error for a developer. It covers nothing else — `.update()` never calls
`save()`, and raw SQL does not know the model exists.

Migration `0002` installs `BEFORE UPDATE`/`BEFORE DELETE` triggers on both
PostgreSQL and SQLite. Those are the rule; the Python guards are the message.
An unknown database backend raises rather than silently installing nothing.

`tests/test_verify.py` **drops those triggers on purpose** before each tamper.
That is the threat model: somebody with enough access to rewrite a row has
enough access to drop a trigger first, and what has to survive that is the hash
chain. The tests prove it does — including a *self-consistent* rewrite, where
the attacker also recomputes the hash of the row they edited and the break shows
up one block later, in a link they did not think to fix.

## What the chain refuses to do

It does not interpret relationships, corrections or reversals. There is no
`supersedes`, no `corrects`, no `reverses` on `LedgerEntry`, and a test asserts
their absence. An action that corrects an earlier one cites that block's `uid`
**in its own body, as ordinary text** — which is what a paper minute book does,
and which means the chain never has to decide what a correction means.

## Signatures

Optional, and applied at append time — `append(signer=…)`. There is no later
moment: the row refuses every write after creation. A signature covers the
block's canonical XML, the same text the hash was taken over, so it commits to
the content *and* the block's place in the chain and cannot be lifted onto
another block.

Key custody is deliberately not this app's problem. `signing.py` takes a key and
returns a callable; `gervazy_signer()` adapts the house answer
(`toto.gervazy`, Ed25519 in a person's strongbox, verification with no password).

## Exports

`chain_document()` is the whole chain as one XML file, headed with the algorithm
and format version. `chain_zip()` is a manifest plus one file per block, with
fixed timestamps so two exports of one chain are byte-for-byte identical and a
diff shows only what actually changed.

## Running the tests

```bash
BUILD_BUSINESS_CENTER=1 python manage.py test \
    toto.ledger.tests.test_canonical toto.ledger.tests.test_chain \
    toto.ledger.tests.test_verify toto.ledger.tests.test_export \
    toto.ledger.tests.test_signing toto.ledger.tests.test_views
```

`toto` is a namespace package — name the modules, never the package.
