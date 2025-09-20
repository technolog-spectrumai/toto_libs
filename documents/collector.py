from pylatex import Document as LatexDoc, Section, Subsection, Package, Command
from pylatex.utils import NoEscape
from django.utils.html import escape
from documents.models import DocumentEditor, LatexDocument, HTMLDocument


class BaseCollector:
    def __init__(self, editor: DocumentEditor):
        self.editor = editor

    def collect(self):
        raise NotImplementedError

    def render(self):
        raise NotImplementedError


class LaTeXCollector(BaseCollector):
    def __init__(self, editor: DocumentEditor):
        super().__init__(editor)
        preset = editor.preset
        self.doc = LatexDoc(documentclass=preset.document_class)

    def build_summary(self):
        if self.editor.summary:
            self.doc.append(NoEscape(r'\begin{abstract}'))
            self.doc.append(NoEscape(self.editor.summary))
            self.doc.append(NoEscape(r'\end{abstract}'))

    def build_preamble(self):
        preset = self.editor.preset
        for pkg in preset.packages:
            self.doc.packages.append(Package(pkg))
        if preset.preamble:
            self.doc.preamble.append(NoEscape(preset.preamble))

    def build_title(self):
        document = self.editor.document.get_real_instance()
        self.doc.preamble.append(Command('title', document.title))
        author = document.department.owner.get_full_name() if document.department else "Unknown"
        self.doc.preamble.append(Command('author', author))
        self.doc.preamble.append(Command('date', NoEscape(r'\today')))
        self.doc.append(NoEscape(r'\maketitle'))

    def build_footer(self):
        preset = self.editor.preset
        if preset.footer_note:
            self.doc.append(NoEscape(r'\vfill'))
            self.doc.append(NoEscape(r'\begin{center}'))
            self.doc.append(NoEscape(r'\textit{' + preset.footer_note + '}'))
            self.doc.append(NoEscape(r'\end{center}'))

    def collect(self):
        self.build_preamble()
        self.build_title()
        self.build_summary()
        for section in self.editor.sections.all():
            with self.doc.create(Section(section.title)):
                if section.content:
                    self.doc.append(NoEscape(section.content))
                for subsection in section.subsections.all():
                    with self.doc.create(Subsection(subsection.title)):
                        if subsection.content:
                            self.doc.append(NoEscape(subsection.content))
        self.build_footer()

    def render(self):
        return self.doc.dumps()


class HTMLCollector(BaseCollector):
    def __init__(self, editor: DocumentEditor):
        super().__init__(editor)
        self.lines = []

    def collect(self):
        for section in self.editor.sections.all():
            self.lines.append(f"<h2>{escape(section.title)}</h2>")
            if section.content:
                self.lines.append(f"<p>{escape(section.content)}</p>")
            for subsection in section.subsections.all():
                self.lines.append(f"<h3>{escape(subsection.title)}</h3>")
                if subsection.content:
                    self.lines.append(f"<p>{escape(subsection.content)}</p>")

    def render(self):
        return "\n".join(self.lines)

def get_collector_for_document(editor: DocumentEditor) -> BaseCollector:
    real_editor = editor.get_real_instance()
    document = real_editor.document.get_real_instance()

    if isinstance(document, LatexDocument):
        return LaTeXCollector(real_editor)
    elif isinstance(document, HTMLDocument):
        return HTMLCollector(real_editor)
    else:
        raise TypeError(f"Unsupported document type: {type(document)}")
