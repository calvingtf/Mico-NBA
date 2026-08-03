"""The spike's offline parts: recall accounting, not network."""

from __future__ import annotations

from mironba.data.ingest.wayback_spike import _norm_url, curated_urls


def test_the_curated_corpus_is_the_recall_denominator():
    counts = curated_urls(2026)
    assert sum(counts.values()) == 26
    assert len(counts) == 4


def test_url_normalisation_matches_cdx_originals():
    assert _norm_url("https://www.hoopsrumors.com/2026/06/x.html") == \
        _norm_url("http://hoopsrumors.com/2026/06/x.html")
