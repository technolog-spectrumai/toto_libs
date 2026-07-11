from django.core.management.base import BaseCommand

from toto.texlab.workflow import ensure_compile_workflow


class Command(BaseCommand):
    help = "Seed the dedicated 'texlab-compile-latex' TeX compile workflow (idempotent)."

    def handle(self, *args, **options):
        wf = ensure_compile_workflow()
        n = wf.nodes.count()
        self.stdout.write(self.style.SUCCESS(
            f"TeX compile workflow ready: slug='{wf.slug}' id={wf.id} ({n} node(s))."
        ))
