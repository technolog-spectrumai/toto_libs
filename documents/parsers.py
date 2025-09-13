from bs4 import BeautifulSoup
from TexSoup import TexSoup
from documents.models import (
    LatexSection, LatexSubSection
)

# ────────────────────────────────────────────────
# 🧱 Base Parser
# ────────────────────────────────────────────────

class BaseParser:
    def __init__(self, config=None):
        self.config = config or {}

    def extract_sections(self, content):
        raise NotImplementedError("Subclasses must implement extract_sections")

    def extract_subsections(self, content):
        raise NotImplementedError("Subclasses must implement extract_subsections")

    def build_sections(self, document, content):
        raise NotImplementedError("Subclasses must implement build_sections")

    def build_subsections(self, section, content):
        raise NotImplementedError("Subclasses must implement build_subsections")


# ────────────────────────────────────────────────
# 🌐 HTML Parser
# ────────────────────────────────────────────────

# class HTMLParser(BaseParser):
#     def extract_sections(self, content):
#         soup = BeautifulSoup(content, "html.parser")
#         return [
#             {
#                 "title": tag.get_text(strip=True),
#                 "tag": tag.name,
#                 "content": self._collect_content(tag)
#             }
#             for tag in soup.find_all(['h1', 'h2'])
#         ]
#
#     def extract_subsections(self, content):
#         soup = BeautifulSoup(content, "html.parser")
#         return [
#             {
#                 "title": tag.get_text(strip=True),
#                 "tag": tag.name,
#                 "content": self._collect_content(tag)
#             }
#             for tag in soup.find_all(['h3', 'h4'])
#         ]
#
#     def build_sections(self, document, content):
#         return [
#             HTMLSection(
#                 document=document,
#                 title=data["title"],
#                 order=i,
#                 content=data["content"]
#             )
#             for i, data in enumerate(self.extract_sections(content))
#         ]
#
#     def build_subsections(self, section, content):
#         return [
#             HTMLSubSection(
#                 section=section,
#                 title=data["title"],
#                 order=i,
#                 content=data["content"]
#             )
#             for i, data in enumerate(self.extract_subsections(content))
#         ]
#
#     def _collect_content(self, tag):
#         content = []
#         for sibling in tag.find_next_siblings():
#             if sibling.name and sibling.name.startswith('h'):
#                 break
#             content.append(sibling.get_text(strip=True))
#         return "\n".join(content)


# ────────────────────────────────────────────────
# 🧪 LaTeX Parser
# ────────────────────────────────────────────────

class LaTeXParser(BaseParser):
    def _unwrap(self, arg):
        return arg.string if hasattr(arg, "string") else str(arg).strip("{}")

    def _flatten(self, contents):
        return "\n".join(str(c).strip() for c in contents if str(c).strip())

    def extract_sections(self, content):
        soup = TexSoup(content)
        return [
            {
                "title": self._unwrap(section.args[0]),
                "content": self._flatten(section.contents)
            }
            for section in soup.find_all('section')
        ]

    def extract_subsections(self, content):
        soup = TexSoup(content)
        return [
            {
                "title": self._unwrap(subsection.args[0]),
                "content": self._flatten(subsection.contents)
            }
            for subsection in soup.find_all('subsection')
        ]

    def build_sections(self, document, content):
        return [
            LatexSection(
                document=document,
                title=data["title"],
                order=i,
                content=data["content"]
            )
            for i, data in enumerate(self.extract_sections(content))
        ]

    def build_subsections(self, section, content):
        return [
            LatexSubSection(
                section=section,
                title=data["title"],
                order=i,
                content=data["content"]
            )
            for i, data in enumerate(self.extract_subsections(content))
        ]
