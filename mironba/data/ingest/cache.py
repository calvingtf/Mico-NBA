"""Polite, cached HTTP fetching.

Two jobs, both about not lying to ourselves later:

  * **Cache to disk.** A re-run must not re-fetch. Ingest is slow because it is
    rate-limited, and a cached run is the difference between iterating on the
    parser in seconds and in minutes.
  * **Record provenance.** Every cached response stores the URL it came from
    and when it was retrieved. A salary that cannot say where it came from is
    the thing the charter forbids, so the metadata is written with the bytes
    rather than reconstructed afterwards.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Basketball-Reference asks for no more than ~20 requests/minute and will
#: return 429 and then block outright. 3.5s keeps us under that with headroom.
#: Seconds between requests to the same host.
#:
#: **Anyone running the backfill from this repo will hit this wall.**
#: Basketball-Reference allows roughly 20 requests per minute and answers a
#: violation with HTTP 429 for about an hour - not a per-request rejection, a
#: block on everything from that address. A seven-season backfill is ~217 page
#: fetches, so it runs for a quarter of an hour with no margin for anything
#: else touching the site at the same time.
#:
#: Two rules follow, learned by breaking both:
#:
#: 1. Do not fetch anything by hand while a backfill is running. The backfill
#:    alone at 3.5s is ~17/min, inside the limit; a few manual verification
#:    fetches alongside it are what actually tripped the ban, two thirds of the
#:    way through the first season.
#: 2. A 429 is not retried. The cache raises FetchError and the season is
#:    reported as incomplete rather than partially loaded, because a snapshot
#:    missing an unknown subset of teams produces payrolls that are wrong in a
#:    direction nothing downstream can detect.
#:
#: Raised from 3.5s after a seven-season backfill was cut off by an HTTP 429.
#: Basketball-Reference allows roughly 20 requests a minute and bans for about
#: an hour on violation; 3.5s is ~17/min, close enough to the line that a few
#: manual fetches alongside a running backfill tripped it. 4.5s is ~13/min,
#: which costs about three minutes per season and does not get the job banned
#: two thirds of the way through.
DEFAULT_MIN_INTERVAL = 4.5

USER_AGENT = "MiroNBA research ingest (single-user, cached, rate-limited)"

_last_request_at = 0.0


class FetchError(RuntimeError):
    """A source could not be retrieved. Never swallowed — an unreachable source
    is reported, never quietly replaced with a substitute."""


@dataclass(frozen=True, slots=True)
class Fetched:
    url: str
    text: str
    retrieved_at: str
    from_cache: bool

    @property
    def retrieved_date(self) -> str:
        return self.retrieved_at[:10]


def _paths(url: str, cache_dir: Path) -> tuple[Path, Path]:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    return cache_dir / f"{key}.html", cache_dir / f"{key}.json"


def fetch(
    url: str,
    cache_dir: Path,
    *,
    force: bool = False,
    min_interval: float = DEFAULT_MIN_INTERVAL,
) -> Fetched:
    """Return ``url``'s body, from disk cache when available."""
    global _last_request_at
    cache_dir.mkdir(parents=True, exist_ok=True)
    body_path, meta_path = _paths(url, cache_dir)

    if not force and body_path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return Fetched(
            url=url,
            text=body_path.read_text(encoding="utf-8"),
            retrieved_at=meta["retrieved_at"],
            from_cache=True,
        )

    wait = min_interval - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raise FetchError(f"{url} -> HTTP {exc.code} {exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001 - surface the cause verbatim
        raise FetchError(f"{url} -> {type(exc).__name__}: {exc}") from exc
    finally:
        _last_request_at = time.monotonic()

    text = raw.decode("utf-8", "replace")
    retrieved_at = datetime.now(UTC).isoformat(timespec="seconds")
    body_path.write_text(text, encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {"url": url, "retrieved_at": retrieved_at, "status": status, "bytes": len(raw)},
            indent=2,
        ),
        encoding="utf-8",
    )
    return Fetched(url=url, text=text, retrieved_at=retrieved_at, from_cache=False)
