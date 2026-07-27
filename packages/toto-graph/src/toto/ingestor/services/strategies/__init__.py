"""Ingestion strategies. Importing this package registers all strategies."""
from .base import MODE_AUTOBUILD, MODE_REVIEW, IngestStrategy  # noqa: F401

# Import for their @IngestStrategy.register side effects (order = display order).
from . import deterministic  # noqa: F401,E402
from . import llm  # noqa: F401,E402
from . import kg_builder  # noqa: F401,E402
