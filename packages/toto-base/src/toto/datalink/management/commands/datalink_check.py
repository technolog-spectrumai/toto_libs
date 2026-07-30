"""Validate the datalink replication contract. Exits non-zero on any problem.

Deliberately shaped like `ravioli_check`: a bullet list and a non-zero exit, so a CI
step or a clean-env gate can depend on it. Runs no queries and touches no peer.
"""
from django.core.management.base import BaseCommand

from toto.datalink.registry import STAGES, load_registry, registry_digest, stage_models
from toto.datalink.validate import describe_registry, validate_registry


class Command(BaseCommand):
    help = "Validate the datalink model registry (no database access, no network)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--plan", action="store_true",
            help="Also print the stage plan: which models each stage writes, in order.",
        )

    def handle(self, *args, **options):
        registry = load_registry()
        replicated = [p for p in registry.values() if not p.refused]
        refused = [p for p in registry.values() if p.refused]

        self.stdout.write(
            f"datalink registry: {len(replicated)} replicated, {len(refused)} refused, "
            f"digest {registry_digest()[:12]}"
        )

        if options["plan"]:
            for stage in STAGES:
                policies = stage_models(stage)
                if not policies:
                    continue
                self.stdout.write(f"\n  {stage}")
                for policy in policies:
                    m2m = f"  +m2m {','.join(policy.m2m)}" if policy.m2m else ""
                    tie = f"  tiebreak={policy.timestamp_field}" if policy.timestamp_field else ""
                    self.stdout.write(
                        f"    {policy.model_label:34} {policy.identity:8}"
                        f"{len(policy.fields):3} fields{m2m}{tie}"
                    )

        for note in describe_registry():
            self.stdout.write(self.style.WARNING(f"\nnote: {note}"))

        problems = validate_registry()
        if problems:
            self.stdout.write(self.style.ERROR(f"\n{len(problems)} problem(s):"))
            for problem in problems:
                self.stdout.write(self.style.ERROR(f"  - {problem}"))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("\nOK: the replication contract is sound"))
