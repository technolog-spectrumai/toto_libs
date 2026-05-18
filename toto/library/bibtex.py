from __future__ import annotations

from .models import Article, AudioReference, Book, LibraryCollection, VideoReference


def _escape_bib(value: str) -> str:
    return value.replace("{", r"\{").replace("}", r"\}")


def _field(name: str, value: str) -> str | None:
    v = value.strip()
    if not v:
        return None
    return f"  {name:<14} = {{{_escape_bib(v)}}}"


def _entry(entry_type: str, key: str, fields: list[tuple[str, str]]) -> str:
    lines = [f"@{entry_type}{{{key},"]
    for name, value in fields:
        line = _field(name, value)
        if line:
            lines.append(line + ",")
    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    lines.append("}")
    return "\n".join(lines)


def book_to_bib_entry(book: Book) -> str:
    return _entry("book", book.slug, [
        ("title", book.title),
        ("author", book.authors),
        ("year", str(book.year) if book.year else ""),
        ("publisher", book.publisher),
        ("edition", book.edition),
        ("isbn", book.isbn),
        ("doi", book.doi),
        ("url", book.url),
    ])


def article_to_bib_entry(article: Article) -> str:
    return _entry("article", article.slug, [
        ("title", article.title),
        ("author", article.authors),
        ("year", str(article.year) if article.year else ""),
        ("journal", article.journal),
        ("volume", article.volume),
        ("number", article.issue),
        ("pages", article.pages),
        ("doi", article.doi),
        ("url", article.url),
    ])


def audio_to_bib_entry(audio: AudioReference) -> str:
    note_parts = ["Audio"]
    if audio.platform:
        note_parts.append(f"Platform: {audio.platform}")
    if audio.duration:
        note_parts.append(f"Duration: {audio.duration}")
    return _entry("misc", audio.slug, [
        ("title", audio.title),
        ("author", audio.authors),
        ("year", str(audio.year) if audio.year else ""),
        ("howpublished", audio.platform),
        ("note", ". ".join(note_parts)),
        ("url", audio.url),
    ])


def video_to_bib_entry(video: VideoReference) -> str:
    note_parts = ["Video"]
    if video.platform:
        note_parts.append(f"Platform: {video.platform}")
    if video.duration:
        note_parts.append(f"Duration: {video.duration}")
    return _entry("misc", video.slug, [
        ("title", video.title),
        ("author", video.authors),
        ("year", str(video.year) if video.year else ""),
        ("director", video.director),
        ("howpublished", video.platform),
        ("note", ". ".join(note_parts)),
        ("url", video.url),
    ])


_DISPATCH = {
    Book: book_to_bib_entry,
    Article: article_to_bib_entry,
    AudioReference: audio_to_bib_entry,
    VideoReference: video_to_bib_entry,
}


def collection_to_bibtex(collection: LibraryCollection) -> str:
    header = f"% BibTeX export — {collection.title}\n\n"
    entries = []
    for book in collection.books.all():
        entries.append(book_to_bib_entry(book))
    for article in collection.articles.all():
        entries.append(article_to_bib_entry(article))
    for audio in collection.audio.all():
        entries.append(audio_to_bib_entry(audio))
    for video in collection.videos.all():
        entries.append(video_to_bib_entry(video))
    return header + "\n\n".join(entries) + "\n"
