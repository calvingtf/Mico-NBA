"""The throughput canary.

Written because ``gpu_fraction: 1.0`` was true through a 3x slowdown. Residency
says where the weights are; it says nothing about whether there is headroom
left to run them in. These tests are about the canary firing when it should and
staying quiet when it should — a canary that cries wolf gets disabled, which is
the same outcome as not having one.
"""

from __future__ import annotations

import json

import pytest

from mironba.llm.canary import (
    CANARY_PROMPT,
    DEFAULT_TOLERANCE,
    ThroughputSample,
    check_throughput,
    load_baseline,
    save_baseline,
)


class Cfg:
    server = "ollama"
    model = "qwen3.6:27b"


@pytest.fixture
def baseline(tmp_path):
    path = tmp_path / "baseline.json"
    save_baseline(Cfg(), ThroughputSample(36.0, 5.0, 180), path=path)
    return path


class TestItFiresWhenItShould:
    def test_a_three_x_slowdown_aborts(self):
        """The case that motivated the whole module: 36 tok/s to 12."""
        assert "slower" in _check(ThroughputSample(12.0, 40.0, 180))

    def test_a_slowdown_just_past_tolerance_aborts(self):
        sample = ThroughputSample(36.0 * (1 - DEFAULT_TOLERANCE) - 0.5, 5.0, 180)
        assert _check(sample)

    def test_the_message_names_the_likely_cause(self):
        """An abort that does not say what to look at gets ignored twice and
        then removed."""
        message = _check(ThroughputSample(12.0, 40.0, 180))
        assert "VRAM" in message
        assert "gpu_fraction" in message
        assert "--set-baseline" in message

    def test_speeding_up_also_aborts(self):
        """Quieter, but it means the stored baseline no longer describes this
        machine — so every earlier comparison against it was against the wrong
        number. That wants a decision, not a default."""
        message = _check(ThroughputSample(60.0, 3.0, 180))
        assert message and "faster" in message


class TestItStaysQuietWhenItShould:
    def test_a_small_wobble_passes(self):
        for rate in (34.0, 36.0, 38.0, 40.0):
            assert _check(ThroughputSample(rate, 5.0, 180)) is None

    def test_exactly_at_tolerance_passes(self):
        assert _check(ThroughputSample(36.0 * (1 - DEFAULT_TOLERANCE), 5.0, 180)) is None

    def test_no_baseline_means_no_opinion(self, tmp_path):
        """A fresh clone has no baseline and must not be blocked by one."""
        assert check_throughput(
            Cfg(), ThroughputSample(3.0, 90.0, 180), path=tmp_path / "absent.json"
        ) is None


class TestTheBaselineFile:
    def test_it_records_what_was_measured(self, baseline):
        stored = load_baseline(baseline)["ollama|qwen3.6:27b"]
        assert stored["tokens_per_s"] == 36.0
        assert stored["prompt"] == CANARY_PROMPT
        assert stored["recorded_at"]

    def test_baselines_are_per_model(self, baseline):
        class Other(Cfg):
            model = "qwen3.6:35b-a3b"

        save_baseline(Other(), ThroughputSample(80.0, 2.0, 180), path=baseline)
        data = load_baseline(baseline)
        assert len(data) == 2
        assert check_throughput(
            Other(), ThroughputSample(80.0, 2.0, 180), path=baseline
        ) is None
        # The other model's much faster baseline must not excuse this one.
        assert check_throughput(
            Cfg(), ThroughputSample(80.0, 2.0, 180), path=baseline
        )

    def test_the_committed_baseline_is_readable(self):
        """The real file, so a malformed commit fails here rather than at the
        start of a six-hour bench."""
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "configs" / "throughput_baseline.json"
        if not path.is_file():
            pytest.skip("no baseline recorded on this machine yet")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data
        for key, entry in data.items():
            assert "|" in key
            assert entry["tokens_per_s"] > 0
            assert entry["prompt"] == CANARY_PROMPT


class TestOverheadRatio:
    def test_a_clean_run_is_near_one(self):
        sample = ThroughputSample(tokens_per_s=36.0, wall_s=5.0, generated_tokens=180)
        assert sample.overhead_ratio == pytest.approx(1.0, abs=0.01)

    def test_thrashing_shows_up_as_a_large_ratio(self):
        """Under memory pressure this machine showed 42s of wall for 13.7s of
        generation. The rate alone would have looked merely slow; the ratio
        says the time went somewhere other than generating."""
        sample = ThroughputSample(tokens_per_s=12.5, wall_s=42.1, generated_tokens=171)
        assert sample.overhead_ratio > 3.0

    def test_a_cold_load_inflates_wall_but_not_the_rate(self):
        """Why the canary keys on generation rate rather than wall clock: a
        cold start is legitimately slow and must not fire the alarm."""
        cold = ThroughputSample(tokens_per_s=36.0, wall_s=200.0, generated_tokens=180)
        assert _check(cold) is None
        assert cold.overhead_ratio > 10


def _check(sample, tolerance=DEFAULT_TOLERANCE):
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "b.json"
        save_baseline(Cfg(), ThroughputSample(36.0, 5.0, 180), path=path)
        return check_throughput(Cfg(), sample, tolerance=tolerance, path=path)
