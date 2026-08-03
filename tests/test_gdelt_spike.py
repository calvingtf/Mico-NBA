"""The GDELT spike's offline parts: dating reasoning, matching, batching."""

from __future__ import annotations

from mironba.data.ingest.gdelt_spike import (
    _norm_url,
    _or_batch,
    claim_matches,
)


class TestTheDatingGuaranteeIsStated:
    def test_seendate_is_an_upper_bound_and_a_conservative_pre_gate(self):
        import mironba.data.ingest.gdelt_spike as spike

        doc = spike.__doc__
        assert "UPPER BOUND on\npublication" in doc or "UPPER BOUND" in doc
        assert "seendate <= freeze admits an item as PRE conservatively" in doc
        assert "can never smuggle a\nPOST item into PRE" in doc.replace(
            "  ", " ") or "smuggle" in doc
        assert "gate on seendate" in doc


class TestOfflineMechanics:
    def test_claim_match_needs_subject_and_team_in_one_title(self):
        articles = [{"title": "Warriors host Yaxel Lendeborg for workout"}]
        row = {"team": "GSW"}
        assert claim_matches(row, articles, "Yaxel Lendeborg")
        assert not claim_matches({"team": "MIA"}, articles, "Yaxel Lendeborg")
        assert not claim_matches(row, articles, "Kingston Flemings")

    def test_or_batches_quote_each_name(self):
        q = _or_batch(["AJ Dybantsa", "Cameron Boozer"])
        assert q == '("AJ Dybantsa" OR "Cameron Boozer")'

    def test_url_normalisation(self):
        assert _norm_url("https://www.hoopsrumors.com/x.html") == \
            _norm_url("http://hoopsrumors.com/x.html")
