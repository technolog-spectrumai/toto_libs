"""Remove the illustrative workflows, keeping the ones apps actually call.

WHY THIS EXISTS. The demo set — Data Pipeline, Parallel Enrichment, Human
Approval Gate and the three report samples — is already gated behind
`FULL_INGRESS=1` in `ingress_mandragora`. But a gate only stops NEW seeding: a
database seeded before that gate landed still carries all six, and nothing
removed them. `FULL_INGRESS=0` on such a host reads as "no demo data" while the
workflow list says otherwise.

WHAT IT WILL NOT TOUCH. Three slugs are looked up by running code, and deleting
any of them breaks a button rather than tidying a list:

    vault-zip        toto.vault's Zip action        (vault/views.py)
    repo-run         git init / push / pull          (repo/dispatch.py)
    antivirus-scan   content screening               (antivirus/workflow.py)

They are refused by name, not merely omitted from the demo list — so a typo in
`--slug` cannot take one out, and a future demo that reuses a functional slug
fails loudly instead of quietly.

DRY RUN BY DEFAULT. Deleting a workflow cascades to its nodes, edges and every
recorded run; a command that does that on a bare `manage.py` invocation is one
mistyped shell history entry away from being a bad afternoon. `--delete` is the
word that means it.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from toto.workflows.models import Workflow

#: Seeded by `ingress_mandragora` under `--full`, referenced by nothing.
DEMO_SLUGS = [
    "data-pipeline",
    "parallel-enrichment",
    "human-approval-gate",
    "metrics-trend-report",
    "channel-mix-report",
    "file-client-demo",
]

#: Looked up by slug at runtime. Never removable through this command.
FUNCTIONAL_SLUGS = {
    "vault-zip": "toto.vault's Zip action",
    "repo-run": "git init / push / pull",
    "antivirus-scan": "content screening",
}


class Command(BaseCommand):
    help = ("Remove the illustrative demo workflows. Dry run unless --delete. "
            "The workflows apps call by slug are refused.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete", action="store_true",
            help="Actually remove them. Without this the command only reports.")
        parser.add_argument(
            "--slug", action="append", default=None,
            help="Remove this slug instead of the built-in demo list. "
                 "Repeatable. A functional slug is still refused.")

    def handle(self, *args, **options):
        wanted = options.get("slug") or DEMO_SLUGS

        protected = [s for s in wanted if s in FUNCTIONAL_SLUGS]
        if protected:
            for slug in protected:
                self.stderr.write(
                    f"{slug}: refused — {FUNCTIONAL_SLUGS[slug]} looks this up "
                    f"by slug at runtime. Removing it breaks that feature.")
            return

        found = list(Workflow.objects.filter(slug__in=wanted))
        if not found:
            self.stdout.write("Nothing to remove — no demo workflows present.")
            return

        for workflow in found:
            runs = workflow.runs.count()
            nodes = workflow.nodes.count()
            self.stdout.write(
                f"{workflow.slug}: {workflow.name} "
                f"({nodes} node(s), {runs} run(s))")

        if not options.get("delete"):
            self.stdout.write(self.style.WARNING(
                f"\nDry run — {len(found)} workflow(s) listed, nothing removed. "
                f"Re-run with --delete to remove them and everything that "
                f"cascades off them (nodes, edges, runs)."))
            return

        count = len(found)
        for workflow in found:
            workflow.delete()
        self.stdout.write(self.style.SUCCESS(f"Removed {count} demo workflow(s)."))
