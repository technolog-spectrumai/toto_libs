"""What the assistant may be asked about a notebook cell.

Python code actions, and the unit is a CELL — which is the one place on this
platform where that unit already existed: ``tpy_run_cell`` takes one cell's
source and writes a result back into that cell, and it is the only true fragment
endpoint in the tree. An AI action wants exactly that request/response shape, so
this borrows the addressing rather than inventing a second one.

No `file_type`: an answer here goes back into a `.tpy` cell, and
`toto.vault.scanning` screens markup, not source. Declaring a type the scanner
knows nothing about would be a no-op dressed as a guarantee.
"""

from toto.core.ai_surfaces import AiSurface, code_actions, registry

registry.register(AiSurface(
    key="mandragora",
    label="Notebooks",
    kind="code",
    icon="fa-solid fa-flask",
    description="Explain, comment or fix the Python in a cell.",
    actions=code_actions("Python"),
))
