FIELD_CLASS = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none "
    "shadow-inner transition focus:ring-2 focus:ring-current/20"
)

FIELD_THEME_CLASS = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark placeholder:text-text-main-dark/45' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light placeholder:text-text-main-light/45'"
)

CHECKBOX_CLASS = "h-4 w-4 rounded"

CHECKBOX_THEME_CLASS = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark' "
    ": 'border-accent-2 bg-primary-bg-light'"
)


def apply_oya_field_styles(fields, *, skip=()):
    for name, field in fields.items():
        if name in skip:
            continue
        field.widget.attrs.setdefault("class", FIELD_CLASS)
        field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)


def apply_oya_checkbox_styles(field):
    field.widget.attrs.setdefault("class", CHECKBOX_CLASS)
    field.widget.attrs.setdefault("x-bind:class", CHECKBOX_THEME_CLASS)
