"""proven — a health check has three outcomes, not two."""

from .coerce import DOWN, UP, NotVerifiable, Verdict, is_healthy, require_healthy
from .health import Confidence, Health
from .probe import SubjectDown, from_result, probe, probe_sync
from .rollup import all_of, any_of, summarise
from .store import HealthStore, InMemoryStore, PersistResult, load, persist

__version__ = "0.1.0"

__all__ = [
    "DOWN",
    "UP",
    "Confidence",
    "Health",
    "HealthStore",
    "InMemoryStore",
    "NotVerifiable",
    "PersistResult",
    "SubjectDown",
    "Verdict",
    "__version__",
    "all_of",
    "any_of",
    "from_result",
    "is_healthy",
    "load",
    "persist",
    "probe",
    "probe_sync",
    "require_healthy",
    "summarise",
]
