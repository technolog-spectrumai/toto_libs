from trix_editor.widgets import CSSAdminCode, TrixEditorWidget

#: The upload script, a static file of this app's.
UPLOAD_SCRIPT = "verbena/trix_upload.js"


class TrixUploadScript:
    """The <script> that sends a picture dropped into the editor to the
    attachment door (2026-10-02), in place of the package's.

    The package's script acted on a 200 alone: a picture the door refused
    hung in the editor at its progress bar with nothing said. Ours
    (`verbena/trix_upload.js`) takes it out again and says why under the
    editor. The tag carries what the file cannot know: the door's address,
    reversed by the name the package gave it (``trix_editor_upload``, which
    a host mounts its own door under), and two sentences for when the door
    gives none, in the reader's language — "Upload failed" for a server error
    or a lost connection, and the door's own size sentence for nginx's bare
    413, at the door's cap (``TRIX_UPLOAD_MAX_BYTES``, 10 MB unless a host
    says otherwise). Rendered on each page, so the language is the page's.
    """

    def __html__(self):
        from django.conf import settings
        from django.templatetags.static import static
        from django.urls import reverse
        from django.utils.html import format_html
        from django.utils.translation import gettext

        cap = int(getattr(settings, "TRIX_UPLOAD_MAX_BYTES", 10 * 1024 * 1024))
        return format_html(
            '<script src="{}" data-upload-url="{}" data-upload-failed="{}" '
            'data-upload-too-large="{}"></script>',
            static(UPLOAD_SCRIPT), reverse("trix_editor_upload"), gettext("Upload failed"),
            gettext("The picture is larger than %(mb)s MB.") % {"mb": cap // (1024 * 1024)})


class LocalTrixEditorWidget(TrixEditorWidget):
    """The package's Trix editor, its two files from this platform's /static/.

    django-trix-editor writes `//unpkg.com/trix@<TRIX_VERSION>/…` into its
    widget's Media, so every member who opened the news editor, and every
    admin form with a Trix field, told unpkg their address (2026-10-01,
    37c.20). The image carries the same release (download_vendor.py,
    `vendor/trix/`, 2.1.15 — the package's default TRIX_VERSION).
    `extend = False` drops the package's two links and, since 2026-10-02,
    its upload script, for ours (`TrixUploadScript`); the admin's dark-theme
    styles are kept as they are. One instance of the script for every
    widget, so a page with several editors loads it once.
    """

    class Media:
        extend = False
        js = [TrixUploadScript(), "vendor/trix/trix.umd.min.js"]
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
