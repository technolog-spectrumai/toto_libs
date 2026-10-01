from trix_editor.widgets import CSSAdminCode, JSCode, TrixEditorWidget


class LocalTrixEditorWidget(TrixEditorWidget):
    """The package's Trix editor, its two files from this platform's /static/.

    django-trix-editor writes `//unpkg.com/trix@<TRIX_VERSION>/…` into its
    widget's Media, so every member who opened the news editor, and every
    admin form with a Trix field, told unpkg their address (2026-10-01,
    37c.20). The image carries the same release (download_vendor.py,
    `vendor/trix/`, 2.1.15 — the package's default TRIX_VERSION).
    `extend = False` drops the package's two links; its upload script and the
    admin's dark-theme styles are kept as they are.
    """

    class Media:
        extend = False
        js = [JSCode(), "vendor/trix/trix.umd.min.js"]
        css = {"all": [CSSAdminCode(), "vendor/trix/trix.css"]}


def use_local_trix(fields) -> None:
    """Give every Trix field among a form's `fields` the local widget.

    On the form's own fields, after it is built, because a form cannot ask
    for it any earlier: `TrixEditorField.formfield` puts the package's widget
    on the field whatever `Meta.widgets` says (it overwrites the `widget`
    argument), so naming LocalTrixEditorWidget there changes nothing.
    """
    for field in fields.values():
        widget = field.widget
        if isinstance(widget, TrixEditorWidget) and not isinstance(widget, LocalTrixEditorWidget):
            local = LocalTrixEditorWidget(attrs=widget.attrs)
            local.is_required = widget.is_required
            field.widget = local
