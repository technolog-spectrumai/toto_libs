from toto.ingress import IngressCommand

from toto.mandragora.models import Cell, ComputeKernel, KernelDependency, Notebook

_DEFAULT_DEPS = [
    ("matplotlib", ""),
    ("numpy", ""),
    ("pandas", ""),
]


class Command(IngressCommand):
    help = "Seed default ComputeKernel and starter Notebook for Mandragora."

    def process(self):
        kernel, kernel_created = ComputeKernel.objects.get_or_create(
            name="Python 3",
            defaults={"timeout_ms": 30000, "env": {}},
        )
        if kernel_created:
            self.stdout.write(self.style.SUCCESS(f"Created kernel: {kernel.name}"))
            for pkg, ver in _DEFAULT_DEPS:
                KernelDependency.objects.get_or_create(
                    kernel=kernel,
                    package_name=pkg,
                    defaults={"version_spec": ver},
                )
                self.stdout.write(self.style.SUCCESS(f"  + dependency: {pkg}{ver or ''}"))
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
                content="import matplotlib.pyplot as plt\nimport numpy as np\n\nx = np.linspace(0, 2 * np.pi, 100)\nplt.plot(x, np.sin(x))\nplt.title('sine wave')\nplt.show()",
                position=2,
            )
            Cell.objects.create(
                notebook=notebook,
                cell_type=Cell.CODE,
                content="import sys\nprint(sys.version)",
                position=3,
            )
        else:
            self.stdout.write(self.style.WARNING(f"Notebook already exists: {notebook.title}"))
