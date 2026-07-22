from toto.ingress import IngressCommand

from toto.texlab.workflow import ensure_compile_workflow


class Command(IngressCommand):
    help = "Seed the dedicated 'texlab-compile-latex' TeX compile workflow (idempotent)."

    def process(self):
        # Functional seed — always runs (not gated on --full): the TeX Compiler's
        # Compile button dispatches WorkflowRuns against this workflow.
        wf = ensure_compile_workflow()
        n = wf.nodes.count()
        self.stdout.write(self.style.SUCCESS(
            f"TeX compile workflow ready: slug='{wf.slug}' id={wf.id} ({n} node(s))."
        ))
