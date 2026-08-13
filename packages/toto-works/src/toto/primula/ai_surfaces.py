"""What the assistant may be asked about a spreadsheet range.

**A range is not prose, and pretending otherwise would make it useless.** What
somebody wants from a column of numbers is a formula, an explanation, or a
tidy-up of labels — not "improve this text". So primula declares its own actions
rather than reusing `prose_actions`, and the shared vocabulary stays honest
about what it means elsewhere.

No `file_type`: a Univer workbook is JSON, but what comes back here is a formula
or a sentence that a human pastes into a cell — it never becomes the file, so
screening it as a workbook would be a check on the wrong thing.
"""

from toto.core.ai_surfaces import Action, AiSurface, registry

_SHEET_SYSTEM = (
    "You work with spreadsheet data. Answer with ONLY what was asked — a "
    "formula, a value, or one short sentence. No preamble, no markdown fences, "
    "no explanation unless the question asks for one."
)

registry.register(AiSurface(
    key="primula",
    label="Spreadsheets",
    kind="sheet",
    icon="fa-solid fa-table-cells",
    description="Write a formula, explain a range, or tidy up labels.",
    actions=(
        Action("formula", "Write a formula", "fa-solid fa-square-root-variable",
               system=_SHEET_SYSTEM, needs_instruction=True,
               instruction_placeholder="sum the paid rows, count blanks…",
               template=("Write ONE spreadsheet formula that does this: "
                         "{instruction}\\n\\nThe selected range is:\\n\\n{selection}")),
        Action("explain", "Explain this range", "fa-solid fa-circle-question",
               system=("You explain spreadsheet data. Answer in prose, briefly."),
               template="What is this range, and what does it show?\\n\\n{selection}"),
        Action("tidy", "Tidy up the labels", "fa-solid fa-broom",
               system=_SHEET_SYSTEM,
               template=("Return these labels cleaned up — consistent case and "
                         "spacing, no other change. One per line, same "
                         "order:\\n\\n{selection}")),
        Action("ask", "Ask about it…", "fa-solid fa-comment",
               system=("You answer questions about spreadsheet data. Answer "
                       "briefly and only from what you were shown."),
               needs_instruction=True,
               instruction_placeholder="which row is the outlier?",
               template="{instruction}\\n\\nThe selected range:\\n\\n{selection}"),
    ),
))
