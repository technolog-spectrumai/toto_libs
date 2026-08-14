from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = ("Seed the antivirus scan workflow so it is visible in the "
            "workflows app from the first deploy, not the first scan.")

    def process(self):
        # ensure_scan_workflow is get-or-create — the same call dispatch makes
        # lazily. Running it at ingress time means an operator opening the
        # workflows app sees the scan workflow before anybody ever pressed
        # Scan, instead of concluding antivirus has no worker path.
        from toto.antivirus.workflow import ensure_scan_workflow

        workflow = ensure_scan_workflow()
        self.stdout.write(self.style.SUCCESS(
            f"Antivirus scan workflow present: {workflow.slug}"))
        # No --full branch: a scan registry with demo verdicts would be a lie,
        # the same reasoning that keeps notarius out of ingress entirely.
