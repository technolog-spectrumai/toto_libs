"""
Seed a sample LaTeX VaultFile for texplay user-testing.

The file is placed in the first available bucket (preferring "Media" or
"General"), owned by the first superuser.  Running this command a second
time is safe: it skips creation if a file with the same key already exists.
"""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile

from toto.ingress import IngressCommand

User = get_user_model()

SAMPLE_TEX = r"""\documentclass{article}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{geometry}
\geometry{a4paper, margin=2.5cm}

\title{Hello from \textbf{toto vault}}
\author{Portal}
\date{\today}

\begin{document}

\maketitle

\section{Introduction}
Welcome to \textbf{toto vault}.  This sample \LaTeX{} document
lets you test the \emph{Play / Compile} feature: click the green
\textbf{Play} button next to this file in the vault file list to
compile it into a PDF.

\section{Mathematics}
Here is the quadratic formula:
\[
  x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}
\]

And Euler's celebrated identity:
\[
  e^{i\pi} + 1 = 0
\]

\section{Lists}
\begin{itemize}
  \item Upload \texttt{.tex} files to the vault.
  \item Click \textbf{Play} to compile them into a PDF.
  \item Download the resulting PDF directly from the compile view.
\end{itemize}

\section{A short poem}
\begin{verse}
  Roses are \texttt{\#FF0000},\\
  violets are \texttt{\#0000FF},\\
  \LaTeX{} compiles,\\
  and so should you.
\end{verse}

\end{document}
"""


class Command(IngressCommand):
    help = "Seed a sample .tex VaultFile for texplay user-testing."

    def process(self):
        from toto.vault.models import Bucket, VaultFile

        owner = User.objects.filter(is_superuser=True).order_by("pk").first()
        if owner is None:
            self.stdout.write(self.style.WARNING("No superuser found — skipping texplay seed."))
            return

        # Prefer the Media bucket, then General, then any bucket owned by superuser.
        bucket = (
            Bucket.objects.filter(slug="media").first()
            or Bucket.objects.filter(slug="general").first()
            or Bucket.objects.filter(owner=owner).first()
        )
        if bucket is None:
            self.stdout.write(self.style.WARNING("No bucket found — run ingress_vault first."))
            return

        key = "sample-latex-document"

        if VaultFile.objects.filter(bucket=bucket, key=key).exists():
            self.stdout.write(self.style.SUCCESS(f"✓ Sample .tex file already exists in bucket '{bucket.slug}'."))
            return

        vf = VaultFile(
            owner=owner,
            title="sample-latex-document.tex",
            bucket=bucket,
            file_type="latex",
            is_public=True,
            key=key,
        )
        vf.file.save(
            "sample-latex-document.tex",
            ContentFile(SAMPLE_TEX.encode()),
            save=False,
        )
        vf.file_size_bytes = len(SAMPLE_TEX.encode())
        vf.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Created sample .tex file (pk={vf.pk}) in bucket '{bucket.slug}'."
            )
        )
