"""Real-data ingest.

One module per source. Raw responses are cached to disk, and every ingested
table records the URL it came from and the date it was retrieved.

Nothing here is imported by ``rules/``. Ingest is fallible and networked; the
rules engine is neither, and the boundary is worth keeping visible.
"""

from mironba.data.ingest.cache import FetchError, Fetched, fetch

__all__ = ["FetchError", "Fetched", "fetch"]
