"""Built-in default LaTeX template for converting a ``.contract`` to PDF.

Seeded by ``ingress_notarius`` (always, even without ``--full``) so a working
template exists out of the box — these are tedious to write by hand. Admins can
copy/customize it per contract type in the admin. Rendered with autoescaping OFF;
text is made LaTeX-safe with the ``|latexescape`` filter.
"""

# A cover/summary page (metadata, parties, signatures with handwritten appearance
# images, audit trail) followed by the embedded original document via \includepdf.
DEFAULT_CONTRACT_LATEX = r"""{% load notarius_latex %}\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[margin=2.2cm]{geometry}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{array}
\usepackage[hidelinks]{hyperref}
\usepackage{pdfpages}
\setlength{\parindent}{0pt}
\renewcommand{\arraystretch}{1.3}

\begin{document}

\begin{center}
  {\LARGE\bfseries {{ contract.title|latexescape }}}\\[4pt]
  {\large Signed document}\\[2pt]
  \rule{\linewidth}{0.4pt}
\end{center}

\vspace{0.4em}
\begin{tabular}{@{}ll@{}}
  \textbf{Document ID} & {{ contract.id|latexescape }} \\
  \textbf{Type}        & {{ contract.doc_type|default:"—"|latexescape }} \\
  \textbf{Status}      & {{ contract.status|latexescape }} \\
  \textbf{Created}     & {{ contract.created_at|latexescape }} \\
\end{tabular}

\section*{Parties}
\begin{longtable}{@{}p{0.18\linewidth} p{0.34\linewidth} p{0.40\linewidth}@{}}
  \toprule
  \textbf{Role} & \textbf{Name} & \textbf{Representative} \\
  \midrule
  {% for p in contract.parties %}{{ p.role|latexescape }} & {{ p.legal_name|latexescape }}{% if p.email %} \newline \texttt{ {{ p.email|latexescape }} }{% endif %} & {% if p.representative %}{{ p.representative.name|latexescape }}{% if p.representative.title %} \newline {{ p.representative.title|latexescape }}{% endif %}{% else %}—{% endif %} \\
  {% endfor %}
  \bottomrule
\end{longtable}

\section*{Signatures}
{% if signature_images %}{% for s in signature_images %}
\vspace{0.6em}
\textbf{ {{ s.party_name|latexescape }} }{% if s.typed_name %} \quad ({{ s.typed_name|latexescape }}){% endif %} \\
\small Method: {{ s.method|default:"electronic"|latexescape }} \quad Signed: {{ s.signed_at|latexescape }} \\
{% if s.filename %}\includegraphics[width=5cm,height=2.5cm,keepaspectratio]{% templatetag openbrace %}{{ s.filename }}{% templatetag closebrace %}\\{% endif %}
\rule{6cm}{0.4pt}
{% endfor %}{% else %}\emph{No signatures yet.}{% endif %}

\section*{Audit trail}
\begin{longtable}{@{}p{0.22\linewidth} p{0.20\linewidth} p{0.46\linewidth}@{}}
  \toprule
  \textbf{Event} & \textbf{Actor} & \textbf{Timestamp} \\
  \midrule
  {% for e in contract.audit %}{{ e.type|latexescape }} & {{ e.actor|default:"—"|latexescape }} & {{ e.timestamp|latexescape }} \\
  {% endfor %}
  \bottomrule
\end{longtable}

{% if content_pdf_filename %}\includepdf[pages=-]{% templatetag openbrace %}{{ content_pdf_filename }}{% templatetag closebrace %}{% endif %}

\end{document}
"""
