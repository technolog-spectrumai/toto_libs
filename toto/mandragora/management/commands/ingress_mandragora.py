from toto.core.ingress import IngressCommand

from toto.mandragora.models import Cell, ComputeKernel, Notebook


class Command(IngressCommand):
    help = "Seed default ComputeKernel and starter Notebook for Mandragora."

    def process(self):
        kernel, kernel_created = ComputeKernel.objects.get_or_create(
            name="Python 3",
            defaults={"timeout_ms": 30000, "env": {}, "dependencies": []},
        )
        if kernel_created:
            self.stdout.write(self.style.SUCCESS(f"Created kernel: {kernel.name}"))
        else:
            self.stdout.write(self.style.WARNING(f"Kernel already exists: {kernel.name}"))

        notebook, nb_created = Notebook.objects.get_or_create(
            slug="getting-started",
            defaults={"title": "Getting Started", "kernel": kernel},
        )
        if nb_created:
            self.stdout.write(self.style.SUCCESS(f"Created notebook: {notebook.title}"))
            Cell.objects.create(
                notebook=notebook,
                cell_type=Cell.CODE,
                content='print("Hello from Mandragora!")',
                position=1,
            )
            Cell.objects.create(
                notebook=notebook,
                cell_type=Cell.CODE,
                content="import sys\nprint(sys.version)",
                position=2,
            )
        else:
            self.stdout.write(self.style.WARNING(f"Notebook already exists: {notebook.title}"))
