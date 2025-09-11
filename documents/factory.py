from django.utils.text import slugify
from TexSoup import TexSoup
from bs4 import BeautifulSoup

from .models import (
    Scratchpad,
    Document,
    HtmlDocument,
    LatexDocument,
    Section,
    HTMLSubSection,
    LaTeXSubSection
)


class DocumentFactory:
    def __init__(self, scratchpad):
        self.scratchpad = scratchpad

    def create_document(self):
        raise NotImplementedError("Subclasses must implement create_document()")


class PlainTextDocumentFactory(DocumentFactory):
    def create_document(self):
        doc = Document.objects.create(
            title=self.scratchpad.title or f"Scratchpad #{self.scratchpad.pk}",
            slug=slugify(self.scratchpad.title or f"Scratchpad-{self.scratchpad.pk}"),
            author=self.scratchpad.user,
            description=self.scratchpad.content,
            status='Draft'
        )
        return doc


class HtmlDocumentFactory(DocumentFactory):
    def create_document(self):
        doc = HtmlDocument.objects.create(
            title=self.scratchpad.title or f"Scratchpad #{self.scratchpad.pk}",
            slug=slugify(self.scratchpad.title or f"Scratchpad-{self.scratchpad.pk}"),
            author=self.scratchpad.user,
            description="Promoted from Scratchpad",
            summary=self.scratchpad.content,
            status='Draft'
        )
        self._parse_html(self.scratchpad.content, doc)
        return doc

    def _parse_html(self, content, document):
        soup = BeautifulSoup(content, 'html.parser')
        section_order = 1

        for section_tag in soup.find_all('section'):
            heading = section_tag.get('data-heading') or f"Section {section_order}"
            section = Section.objects.create(document=document, order=section_order, heading=heading)

            subsection_order = 1
            for sub_tag in section_tag.find_all('div', class_='subsection'):
                title = sub_tag.get('data-title') or f"Subsection {subsection_order}"
                HTMLSubSection.objects.create(
                    section=section,
                    order=subsection_order,
                    title=title,
                    content=str(sub_tag)
                )
                subsection_order += 1

            section_order += 1


class LatexDocumentFactory(DocumentFactory):
    def create_document(self):
        doc = LatexDocument.objects.create(
            title=self.scratchpad.title or f"Scratchpad #{self.scratchpad.pk}",
            slug=slugify(self.scratchpad.title or f"Scratchpad-{self.scratchpad.pk}"),
            author=self.scratchpad.user,
            description="Promoted from Scratchpad",
            compile_flags={},
            status='Draft'
        )
        self._parse_latex(self.scratchpad.content, doc)
        return doc

    def _parse_latex(self, content, document):
        soup = TexSoup(content)
        section_order = 1

        for section_cmd in soup.find_all('section'):
            heading = str(section_cmd.string).strip()
            section = Section.objects.create(document=document, order=section_order, heading=heading)

            subsection_order = 1
            for sub_cmd in section_cmd.find_all('subsection'):
                title = str(sub_cmd.string).strip()
                body = ''.join(str(x) for x in sub_cmd.contents).strip()
                LaTeXSubSection.objects.create(
                    section=section,
                    order=subsection_order,
                    title=title,
                    content=body,
                    use_light_mode=False
                )
                subsection_order += 1

            section_order += 1


def get_document_factory(scratchpad):
    if scratchpad.content_type == 'html':
        return HtmlDocumentFactory(scratchpad)
    elif scratchpad.content_type == 'latex':
        return LatexDocumentFactory(scratchpad)
    elif scratchpad.content_type == 'plain':
        return PlainTextDocumentFactory(scratchpad)
    else:
        raise ValueError(f"Unsupported content type: {scratchpad.content_type}")
