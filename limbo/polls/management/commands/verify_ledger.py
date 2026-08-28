"""Verify every decision ledger on this host, offline-auditor style.

Walks every scope that holds decisions, recomputes the chain AND the
checkpoint fold, and cross-checks each stored checkpoint. Exit code 1 on any
failure, so a cron or a CI step can watch the ledger without a browser.
An operator holding a printed QR pastes its text with ``--payload`` and gets
the same MATCH / MISMATCH verdict the web page gives — the command trusts
nothing but the rows it recomputes from.
"""
from django.core.management.base import BaseCommand

from toto.polls import checkpoint
from toto.polls.checkpoint_models import LedgerCheckpoint
from toto.polls.models import Decision


class Command(BaseCommand):
    help = "Recompute every decision chain and verify stored checkpoints."

    def add_arguments(self, parser):
        parser.add_argument(
            "--payload", default="",
            help="A checkpoint payload (the QR text) to verify against the "
                 "stored ledger, e.g. scanned from a printout.")

    def handle(self, *args, **options):
        failed = False

        if options["payload"]:
            result = checkpoint.verify(
                checkpoint.parse_payload(options["payload"]))
            self._report("pasted payload", result)
            if result.verdict not in ("MATCH", "MATCH_GROWN"):
                raise SystemExit(1)
            return

        scopes = (Decision.objects.values_list("scope_type", "scope_id")
                  .distinct().order_by("scope_type", "scope_id"))
        for scope_type, scope_id in scopes:
            label = f"{scope_type or 'global'}:{scope_id or '-'}"
            chain = Decision.verify_chain(scope_type, scope_id)
            if chain.ok:
                self.stdout.write(
                    f"{label}: chain intact ({chain.checked} entries)")
            else:
                failed = True
                self.stdout.write(self.style.ERROR(
                    f"{label}: CHAIN BROKEN at pk={chain.first_bad_pk} "
                    f"({chain.checked} entries verified before it)"))

            for stored in LedgerCheckpoint.objects.filter(
                    scope_type=scope_type, scope_id=scope_id):
                result = checkpoint.verify_stored(stored)
                self._report(
                    f"{label} checkpoint @{stored.entry_count} "
                    f"({stored.taken_at:%Y-%m-%d %H:%M})", result)
                if result.verdict not in ("MATCH", "MATCH_GROWN"):
                    failed = True

        if failed:
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("Every ledger verifies."))

    def _report(self, label, result):
        style = (self.style.SUCCESS
                 if result.verdict in ("MATCH", "MATCH_GROWN")
                 else self.style.ERROR)
        line = f"{label}: {result.verdict}"
        if result.detail:
            line += f" — {result.detail}"
        self.stdout.write(style(line))
