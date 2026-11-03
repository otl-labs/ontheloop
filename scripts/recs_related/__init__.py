"""scripts.recs_related -- open recommendations related to files (Decision 215 cl.3, rec-4235).

Facade (Decision 124 pattern): re-exports the core, the channel entry points and the envelope types. Note that
`related_recs` here is the FUNCTION; the module of that name is `scripts.recs_related.related_recs` (import it with
importlib or `from scripts.recs_related.related_recs import ...`, since the package attribute is shadowed).
"""

from scripts.recs_related.envelope import Envelope, Hit
from scripts.recs_related.history import HistoryIndex, load_history
from scripts.recs_related.mentions import mentions_of
from scripts.recs_related.neighbourhood import neighbours_of
from scripts.recs_related.related_recs import related_recs
from scripts.recs_related.terms import derive_terms, free_text_terms

__all__ = [
    "Envelope",
    "HistoryIndex",
    "Hit",
    "derive_terms",
    "free_text_terms",
    "load_history",
    "mentions_of",
    "neighbours_of",
    "related_recs",
]
