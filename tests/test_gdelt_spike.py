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


LENDEBORG_URL = ("https://www.hoopsrumors.com/2026/06/"
                 "draft-notes-lendeborg-warriors-wilson-suder-kayil.html")


def _batch(label, names, articles, capped, path,
           window=("20260501000000", "20260624000000")):
    from mironba.data.ingest.gdelt_spike import write_articles

    padded = list(articles)
    if capped:
        padded += [{"url": f"http://filler/{label}/{i}", "title": "x",
                    "seendate": "20260623T000000Z", "domain": "filler"}
                   for i in range(250 - len(padded))]
    write_articles("174.195.129.132", label, window, names, padded, path)


class TestPersistBeforeTheVerdict:
    def test_each_query_lands_on_disk_so_a_later_failure_loses_nothing(self, tmp_path):
        import json

        path = tmp_path / "articles.jsonl"
        _batch("draft volume", [], [{"url": "http://a", "title": "t",
                                     "seendate": "20260622T000000Z",
                                     "domain": "d"}], False, path)
        _batch("draft subjects 1/3", ["Yaxel Lendeborg"],
               [{"url": "http://b", "title": "t2",
                 "seendate": "20260613T000000Z", "domain": "d"}], False, path)
        # the "failure" happens here: nothing else is written - and both
        # earlier batches survive, stamped with egress and label
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        assert [r["label"] for r in rows] == ["draft volume", "draft subjects 1/3"]
        assert all(r["egress_ip"] == "174.195.129.132" for r in rows)

    def test_the_articles_writer_is_declared_append_only(self):
        from mironba.data.ingest import gdelt_spike

        assert "write_articles" in gdelt_spike.APPEND_ONLY


class TestOfflineRecall:
    def _seed(self, path):
        _batch("draft subjects 1/3", ["Yaxel Lendeborg"],
               [{"url": LENDEBORG_URL,
                 "title": "Warriors host Yaxel Lendeborg for workout",
                 "seendate": "20260613T120000Z", "domain": "hoopsrumors.com"}],
               False, path)
        _batch("draft volume", [],
               [{"url": "http://recent", "title": "draft roundup",
                 "seendate": "20260622T000000Z", "domain": "d"}], True, path)

    def test_recall_and_truncation_from_persisted_batches_only(self, tmp_path):
        from mironba.data.ingest.gdelt_spike import offline_recall

        path = tmp_path / "articles.jsonl"
        self._seed(path)
        result = offline_recall(path)
        # 8 curated rows share the Lendeborg URL -> exact hits
        assert result["exact"] == 8
        # GSW x Lendeborg matches the title; other teams do not
        assert result["claims"] == 1
        never = {r["id"] for r in result["never_searched"]}
        # Lendeborg rows (2026-06-13) are covered by the UNCAPPED subject
        # batch; everything else is capped-volume-only, collapsed to
        # 20260622.., so never fully searched
        assert "DI-02" not in never
        assert "DI-01" in never and "DI-10" in never and "DI-18" in never
        assert not result["lebron_measured"]

    def test_a_capped_batch_covers_only_its_tail(self, tmp_path):
        from mironba.data.ingest.gdelt_spike import _effective_window

        path = tmp_path / "articles.jsonl"
        self._seed(path)
        import json

        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        capped = next(r for r in rows if r["capped"])
        uncapped = next(r for r in rows if not r["capped"])
        assert _effective_window(capped) == ("20260622", "20260624")
        assert _effective_window(uncapped) == ("20260501", "20260624")

    def test_the_empty_state_says_what_was_lost(self, tmp_path, capsys):
        from mironba.data.ingest.gdelt_spike import render_offline

        render_offline(tmp_path / "absent.jsonl")
        out = capsys.readouterr().out
        assert "no persisted batches" in out
        assert "discarded" in out

    def test_the_lebron_half_is_never_inferred(self, tmp_path, capsys):
        from mironba.data.ingest.gdelt_spike import render_offline

        path = tmp_path / "articles.jsonl"
        self._seed(path)
        render_offline(path)
        out = capsys.readouterr().out
        assert "UNMEASURED" in out
        assert "not inferred from the draft half" in out
