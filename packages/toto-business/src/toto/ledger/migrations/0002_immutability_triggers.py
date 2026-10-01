"""Refuse UPDATE and DELETE on ledger rows at the database.

`LedgerEntry.save()` and `.delete()` already raise, and that covers the ORM.
It covers nothing else: `.update()` on a queryset never calls `save()`, and a
raw `UPDATE` or a psql prompt does not know the model exists. The Python guards
are the error message a developer sees; these triggers are the rule.

Written for both engines this host runs on. PostgreSQL gets a trigger function
per table; SQLite gets `BEFORE UPDATE`/`BEFORE DELETE` triggers with
`RAISE(ABORT, …)` — the same refusal in the dialect each one speaks.

**Why the Ledger row itself stays mutable.** A chain's name and description are
labels, not evidence: nothing hashes them. What must not move is the algorithm
and the format version, and they are protected the right way instead — the
genesis block records them, `chain_algorithm()` reads the genesis copy, and the
columns on Ledger are a convenience for querying that no verifier trusts.
"""

from django.db import migrations

ENTRY_TABLE = "ledger_ledgerentry"

POSTGRES_UP = f"""
CREATE OR REPLACE FUNCTION bc_ledger_entry_is_frozen() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'ledger entries are append-only: % on %.% is refused',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bc_ledger_entry_no_update ON {ENTRY_TABLE};
CREATE TRIGGER bc_ledger_entry_no_update
    BEFORE UPDATE ON {ENTRY_TABLE}
    FOR EACH ROW EXECUTE FUNCTION bc_ledger_entry_is_frozen();

DROP TRIGGER IF EXISTS bc_ledger_entry_no_delete ON {ENTRY_TABLE};
CREATE TRIGGER bc_ledger_entry_no_delete
    BEFORE DELETE ON {ENTRY_TABLE}
    FOR EACH ROW EXECUTE FUNCTION bc_ledger_entry_is_frozen();
"""

POSTGRES_DOWN = f"""
DROP TRIGGER IF EXISTS bc_ledger_entry_no_update ON {ENTRY_TABLE};
DROP TRIGGER IF EXISTS bc_ledger_entry_no_delete ON {ENTRY_TABLE};
DROP FUNCTION IF EXISTS bc_ledger_entry_is_frozen();
"""

SQLITE_UP = f"""
DROP TRIGGER IF EXISTS bc_ledger_entry_no_update;
CREATE TRIGGER bc_ledger_entry_no_update
BEFORE UPDATE ON {ENTRY_TABLE}
BEGIN
    SELECT RAISE(ABORT, 'ledger entries are append-only: UPDATE is refused');
END;

DROP TRIGGER IF EXISTS bc_ledger_entry_no_delete;
CREATE TRIGGER bc_ledger_entry_no_delete
BEFORE DELETE ON {ENTRY_TABLE}
BEGIN
    SELECT RAISE(ABORT, 'ledger entries are append-only: DELETE is refused');
END;
"""

SQLITE_DOWN = """
DROP TRIGGER IF EXISTS bc_ledger_entry_no_update;
DROP TRIGGER IF EXISTS bc_ledger_entry_no_delete;
"""


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        # params=None, NOT the default (). With params=(), Django hands the SQL
        # to psycopg's mogrify, which reads `%` as a parameter placeholder — and
        # the RAISE EXCEPTION above is a plpgsql FORMAT STRING carrying three of
        # them. The result is `IndexError: tuple index out of range` from deep
        # inside the driver, during migrate, on Postgres only.
        #
        # It only shows up on a real deployment: the gate runs its migration
        # stage against SQLite, so this branch never executes there.
        schema_editor.execute(POSTGRES_UP, params=None)
    elif vendor == "sqlite":
        # executescript, not execute: SQLite's driver refuses more than one
        # statement per execute(), and this is five.
        with schema_editor.connection.cursor() as cursor:
            cursor.executescript(SQLITE_UP)
    # Any other vendor gets the Python guards only, and says so rather than
    # pretending. A silent no-op here would read as protection that is not there.
    else:  # pragma: no cover - no other engine is configured on this host
        raise RuntimeError(
            f"No ledger immutability triggers exist for the {vendor!r} backend. "
            "Add them before running the Business Center on it."
        )


def remove(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        # params=None for the same reason, and kept even though this SQL has no
        # `%` today: the two halves are edited together, and a `%` added to the
        # down path would fail only on a rollback.
        schema_editor.execute(POSTGRES_DOWN, params=None)
    elif vendor == "sqlite":
        with schema_editor.connection.cursor() as cursor:
            cursor.executescript(SQLITE_DOWN)


class Migration(migrations.Migration):

    dependencies = [("ledger", "0001_initial")]

    operations = [migrations.RunPython(install, remove)]
