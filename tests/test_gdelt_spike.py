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


class TestResultsAreSelfInterpreting:
    def test_the_run_record_is_append_only_and_carries_the_egress(self, tmp_path):
        from mironba.data.ingest.gdelt_spike import write_run_record

        path = tmp_path / "runs.jsonl"
        write_run_record({"egress_ip": "1.2.3.4", "verdict": "throttled"}, path)
        write_run_record({"egress_ip": "5.6.7.8", "verdict": "answered"}, path)
        import json

        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        assert [r["egress_ip"] for r in rows] == ["1.2.3.4", "5.6.7.8"]
        assert all("verdict" in r for r in rows)

    def test_the_writer_is_declared_append_only(self):
        from mironba.data.ingest import gdelt_spike

        assert "write_run_record" in gdelt_spike.APPEND_ONLY

    def test_egress_is_fetched_before_any_gdelt_request(self):
        """Structural: main() computes the egress before the query loop, and
        the header line carries it - a throttle verdict is labelled with the
        network that produced it."""
        import inspect

        from mironba.data.ingest import gdelt_spike

        src = inspect.getsource(gdelt_spike.main)
        assert src.index("egress_ip()") < src.index("_query")
        assert "egress IP {egress}" in src
        assert "from " in src and "egress {egress}" in src
