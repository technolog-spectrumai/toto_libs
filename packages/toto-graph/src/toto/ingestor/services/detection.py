"""spaCy-based entity detection (deterministic; no LLM).

* An **EntityRuler** built from the catalog matches *existing* graph entities —
  each pattern carries the node ``uid`` (ent_id) and category slug (label).
* The base model's **NER** suggests *new* entities, mapped to Bento categories
  via :func:`config.label_map`.

The pipeline is loaded lazily and cached. When ``en_core_web_sm`` is missing we
fall back to a blank English pipeline (+ sentencizer): existing-entity matching
and sentence segmentation still work; only statistical new-entity suggestions are
unavailable. spaCy inference is deterministic, satisfying the "no-LLM,
deterministic" constraint.
"""

from dataclasses import dataclass, field

from . import config
from .text import normalize

# Cache one pipeline per (model availability) — patterns are swapped per call so
# the catalog stays fresh without reloading the model.
_NLP = None          # None = not tried; False = spaCy unavailable
_NLP_HAS_NER = False
_RULER_NAME = "ingestor_entity_ruler"


@dataclass
class Mention:
    text: str
    start_char: int
    end_char: int
    sent_index: int
    kind: str                       # "existing" | "new"
    category_slug: str = None       # resolved/guessed category (may be None for new)
    uid: str = None                 # set for existing mentions
    ner_label: str = None           # spaCy label for new mentions
    normalized: str = field(default="")

    def __post_init__(self):
        if not self.normalized:
            self.normalized = normalize(self.text)


@dataclass
class Detection:
    mentions: list
    sentences: list                 # list of sentence strings (by index)
    sentence_forms: list            # per-sentence sets of lemma/orth forms
    ner_available: bool
    spacy_available: bool


def _load_pipeline():
    """Return ``(nlp, has_ner)`` or ``(None, False)`` when spaCy is absent."""
    global _NLP, _NLP_HAS_NER
    if _NLP is None:
        try:
            import spacy
        except ImportError:
            _NLP = False
            _NLP_HAS_NER = False
            return None, False
        try:
            nlp = spacy.load(config.SPACY_MODEL)
            has_ner = nlp.has_pipe("ner")
        except OSError:
            nlp = spacy.blank("en")
            nlp.add_pipe("sentencizer")
            has_ner = False
        _NLP = nlp
        _NLP_HAS_NER = has_ner
    return (_NLP, _NLP_HAS_NER) if _NLP is not False else (None, False)


def _install_ruler(nlp, entries):
    """(Re)install an EntityRuler carrying the catalog patterns.

    Labels are category slugs; ids are node uids. ``overwrite_ents`` lets exact
    existing-entity matches win over the statistical NER. With an empty catalog
    no ruler is added (avoids spaCy's W036 "no patterns" warning) — NER still runs.
    """
    if nlp.has_pipe(_RULER_NAME):
        nlp.remove_pipe(_RULER_NAME)
    patterns, seen = [], set()
    for e in entries:
        key = (e.category_slug, e.normalized, e.uid)
        if key in seen:
            continue
        seen.add(key)
        patterns.append({"label": e.category_slug, "pattern": e.surface, "id": e.uid})
    if not patterns:
        return None
    cfg = {"phrase_matcher_attr": "LOWER", "overwrite_ents": True}
    before = "ner" if nlp.has_pipe("ner") else None
    if before:
        ruler = nlp.add_pipe("entity_ruler", name=_RULER_NAME, before=before, config=cfg)
    else:
        ruler = nlp.add_pipe("entity_ruler", name=_RULER_NAME, config=cfg)
    ruler.add_patterns(patterns)
    return ruler


def _resolve_new_category(ner_label, known_slugs):
    slug = config.label_map().get(ner_label)
    return slug if slug in known_slugs else None


def detect(text, catalog_entries, known_category_slugs=None):
    """Run detection over ``text`` and return a :class:`Detection`.

    ``known_category_slugs`` bounds NER→category mapping to categories that exist.
    """
    known_slugs = set(known_category_slugs or [])
    nlp, has_ner = _load_pipeline()
    if nlp is None:
        # Hard fallback: no spaCy at all → no detection (the view surfaces this).
        return Detection(
            mentions=[], sentences=[text], sentence_forms=[set(normalize(text).split())],
            ner_available=False, spacy_available=False,
        )

    _install_ruler(nlp, catalog_entries)
    doc = nlp(text)

    sents = list(doc.sents) if doc.has_annotation("SENT_START") else [doc[:]]
    sentences = [s.text for s in sents]
    sent_bounds = [(s.start_char, s.end_char) for s in sents]
    sentence_forms = []
    for sent in sents:
        bag = set()
        for tok in sent:
            if tok.is_space or tok.is_punct:
                continue
            if tok.lemma_:
                bag.add(tok.lemma_.lower())
            bag.add(tok.lower_)
        sentence_forms.append(bag)

    def sent_index_for(char):
        for i, (lo, hi) in enumerate(sent_bounds):
            if lo <= char < hi:
                return i
        return 0

    mentions = []
    for ent in doc.ents:
        if ent.ent_id_:  # ruler match → existing graph node
            mentions.append(Mention(
                text=ent.text, start_char=ent.start_char, end_char=ent.end_char,
                sent_index=sent_index_for(ent.start_char), kind="existing",
                category_slug=ent.label_, uid=ent.ent_id_,
            ))
        else:            # statistical NER → suggested new entity
            mentions.append(Mention(
                text=ent.text, start_char=ent.start_char, end_char=ent.end_char,
                sent_index=sent_index_for(ent.start_char), kind="new",
                category_slug=_resolve_new_category(ent.label_, known_slugs),
                ner_label=ent.label_,
            ))

    return Detection(
        mentions=mentions, sentences=sentences, sentence_forms=sentence_forms,
        ner_available=has_ner, spacy_available=True,
    )
