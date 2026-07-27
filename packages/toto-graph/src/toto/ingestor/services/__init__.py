"""Ingestor service layer — deterministic, no LLMs.

text → catalog (existing graph entities) → detection (spaCy EntityRuler + NER)
     → matching (rapidfuzz duplicate detection) → relations (Bento-constrained)
     → scoring → validation (Bento) → proposal JSON → apply (bento.graph_service)

Nothing here imports neo4j/neomodel/spacy/rapidfuzz at module load — heavy deps
are imported lazily so the app stays importable when they're absent.
"""
