"""civic: traceable adapters for official and open policy sources.

    from civic import search
    envelope = search("paid family caregiver", scope="policy", since="2025-01-01")
    envelope.to_dict()  # JSON-ready; every record carries provenance
"""

__version__ = "0.7.0"

from .adapters import ADAPTERS, Adapter, Query
from .http import SourceError, Transport
from .record import Record
from .scope import Scope, parse_scope
from .search import SCHEMA, Envelope, search

__all__ = [
    "ADAPTERS",
    "SCHEMA",
    "Adapter",
    "Envelope",
    "Query",
    "Record",
    "Scope",
    "SourceError",
    "Transport",
    "__version__",
    "parse_scope",
    "search",
]
