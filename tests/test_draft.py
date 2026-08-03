"""Draft sim v0: rumor-driven, projections fenced out, nothing invented."""

from __future__ import annotations

from pathlib import Path

from mironba.sim.draft import (
    UNRESOLVED,
    Slot,
    build_slots,
    load_interest,
    run_draft,
    targets_by_team,
)

ROOT = Path(__file__).resolve().parents[1] / "mironba"


def _slots(owners):
    return [Slot(number=i + 1, round=1, original_owner=t, owner=t)
            for i, t in enumerate(owners)]


class TestOwnership:
    def test_sixty_slots_and_every_one_is_accounted_for(self):
        slots = build_slots(2026)
        assert len(slots) == 60
        assert all(s.owner or s.via == "UNATTRIBUTABLE" for s in slots)

    def test_an_unattributable_slot_is_reported_not_guessed(self):
        slots = build_slots(2026)
        unattr = [s for s in slots if s.via == "UNATTRIBUTABLE"]
        for s in unattr:
            assert s.owner == "" and s.note, "a guess wearing an empty note"

    def test_round_one_order_is_standings_worst_first(self):
        slots = build_slots(2026)
        assert slots[0].number == 1 and slots[0].round == 1
        assert [s.round for s in slots[:30]] == [1] * 30


class TestAssignment:
    def test_deterministic(self):
        interest = load_interest(2026)
        targets = targets_by_team(interest)
        first = run_draft(build_slots(2026), targets)
        second = run_draft(build_slots(2026), targets)
        assert [(a.slot.number, a.player, a.status)
                for a in first.assignments] == \
               [(a.slot.number, a.player, a.status)
                for a in second.assignments]

    def test_contested_prospects_resolve_by_pick_order(self):
        targets = {"AAA": ["Prospect X"], "BBB": ["Prospect X", "Prospect Y"]}
        result = run_draft(_slots(["AAA", "BBB"]), targets)
        assert result.assignments[0].player == "Prospect X"
        assert result.assignments[1].player == "Prospect Y"
        assert result.assignments[1].first_choice_gone

    def test_a_team_with_no_targets_is_unresolved_never_invented(self):
        result = run_draft(_slots(["AAA", "ZZZ"]), {"AAA": ["Prospect X"]})
        second = result.assignments[1]
        assert second.status == UNRESOLVED
        assert "no rumored targets" in second.reason
        assert second.player == ""

    def test_exhausted_targets_are_unresolved_with_the_reason(self):
        targets = {"AAA": ["P1"], "BBB": ["P1"]}
        result = run_draft(_slots(["AAA", "BBB"]), targets)
        assert result.assignments[1].status == UNRESOLVED
        assert "all taken" in result.assignments[1].reason

    def test_priority_is_report_date_then_row_order(self):
        interest = [
            {"id": "B", "team": "AAA", "player": "Late", "date": "2026-06-20"},
            {"id": "A", "team": "AAA", "player": "Early", "date": "2026-06-01"},
        ]
        assert targets_by_team(interest)["AAA"] == ["Early", "Late"]


class TestTheProjectionFence:
    def test_no_sim_path_reads_projections(self):
        """draft_projection is a competing forecaster, not an input. If any
        module outside eval/ can reach the projections file, the sim can be
        seeded with the baseline it is scored against."""
        offenders = []
        for package in ("sim", "agents", "world", "models", "rules", "data"):
            for path in (ROOT / package).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "projections.csv" in text or "draft_projection" in text:
                    offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, (
            f"projection rows reach the simulation side: {offenders}"
        )

    def test_ground_truth_is_only_reachable_from_eval(self):
        offenders = []
        for package in ("sim", "agents", "world", "models", "rules", "data"):
            for path in (ROOT / package).rglob("*.py"):
                if "actual-picks" in path.read_text(encoding="utf-8"):
                    offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, f"ground truth reachable from: {offenders}"

    def test_the_interest_loader_knows_nothing_about_mocks(self):
        src = (ROOT / "sim" / "draft.py").read_text(encoding="utf-8")
        assert "projections.csv" not in src


class TestScoring:
    def test_score_reports_denominator_beside_accuracy(self):
        from mironba.eval.draft_score import score

        s = score(2026, trials=200)
        assert s["resolved"] + s["unresolved"] == 60
        assert 0 <= s["hits"] <= s["resolved"]
        assert s["null2_slots"] <= s["resolved"]

    def test_null1_is_seeded_and_reproducible(self):
        from mironba.eval.draft_score import score

        a = score(2026, trials=500, seed=7)["null1_expected"]
        b = score(2026, trials=500, seed=7)["null1_expected"]
        assert a == b


class TestTheConditionalIsScoredHonestly:
    def test_n_is_two_and_stated_before_any_rate(self, capsys):
        from mironba.eval.draft_score import print_conditional

        print_conditional(2026)
        out = capsys.readouterr().out
        assert out.index("n = 2") < out.index("fallback hits")

    def test_the_conditional_walks_the_actual_draft_not_the_reconstruction(self):
        from mironba.eval.draft_score import conditional_score

        c = conditional_score(2026)
        assert c["conditional_events"] == 20
        assert c["exhausted"] == 18, "single-target teams cannot cascade"
        assert c["n"] == 2 and c["hits"] == 0

    def test_the_null_shrinks_to_the_remaining_set_and_counts_informative_only(self):
        from mironba.eval.draft_score import conditional_score

        c = conditional_score(2026)
        assert c["n_informative"] == 1
        assert abs(c["null_expected"] - 0.25) < 1e-9
        uninformative = [x for x in c["cases"] if not x["informative"]]
        assert len(uninformative) == 1
        assert uninformative[0]["team"] == "NOP"
