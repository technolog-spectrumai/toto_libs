"""Settings-backed knobs for the Ingestor pipeline (all overridable in settings)."""

from django.conf import settings

# spaCy model used for new-entity NER. If absent, detection degrades to a blank
# English pipeline (existing-entity EntityRuler + sentence segmentation still work).
SPACY_MODEL = getattr(settings, "INGESTOR_SPACY_MODEL", "en_core_web_sm")

# Default spaCy NER label → Bento category *slug*. Only used when a category with
# that slug actually exists; otherwise the new entity is left uncategorized for
# the user to classify. Override with INGESTOR_SPACY_LABEL_MAP.
DEFAULT_LABEL_MAP = {
    "PERSON": "person",
    "ORG": "organization",
    "GPE": "place",
    "LOC": "place",
    "FAC": "place",
    "PRODUCT": "product",
    "EVENT": "event",
    "WORK_OF_ART": "work",
    "LAW": "law",
    "NORP": "group",
    "DATE": "date",
    "TIME": "time",
    "MONEY": "money",
}


def label_map():
    return getattr(settings, "INGESTOR_SPACY_LABEL_MAP", DEFAULT_LABEL_MAP)


def catalog_max_nodes():
    return int(getattr(settings, "INGESTOR_CATALOG_MAX_NODES", 20000))


def dup_threshold():
    """Score at/above which a new entity is flagged a likely duplicate."""
    return float(getattr(settings, "INGESTOR_DUP_THRESHOLD", 0.90))


def candidate_threshold():
    """Score at/above which an existing node is offered as a merge candidate."""
    return float(getattr(settings, "INGESTOR_CANDIDATE_THRESHOLD", 0.70))


def fuzzy_max_candidates():
    """Cap on catalog entries scored per surface to bound fuzzy-match cost."""
    return int(getattr(settings, "INGESTOR_FUZZY_MAX_CANDIDATES", 5000))
