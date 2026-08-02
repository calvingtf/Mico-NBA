"""The product surface must show the limitations, not hide them.

A demo is the part of a project people actually look at, and this one sits on
top of measurements that are mostly negative: precision of 1 in 421, predictive
recall of 0 of 1, a value model that does not beat regression to the mean. A
surface that read as a working predictor would misrepresent all of it.

So the guarantees here are structural rather than editorial. The limitation
block is a module constant appended after the model's prose, and the claim
filter drops sentences by pattern. These tests assert both survive into every
rendered output — the terminal report, and the HTML.
"""

from __future__ import annotations

import html as H
import json

import pytest

from mironba.agents.chat import (
    financial_answer,
    looks_financial,
    options_shown,
)
from mironba.agents.report import (
    FORBIDDEN,
    LIMITATIONS,
    BranchSummary,
    Report,
    build_report,
    feed_digest,
    filter_claims,
)
from mironba.report.html import render_html
from mironba.report.timeline import build_feed
from mironba.world.events import EventType

EVENTS = [
    {"seq": 0, "ts": "2026-07-31T08:18:07.000+00:00", "type": EventType.RUN_STARTED,
     "actor": "system", "payload": {"scenario": "curry-to-lakers", "team": "LAL",
                                    "partner": "GSW"}},
    {"seq": 1, "ts": "2026-07-31T08:18:07.100+00:00", "type": EventType.TARGET_SCAN,
     "actor": "solver", "payload": {"considered": "14", "feasible": "['a','b']",
                                    "ceiling": "154856190"}},
    {"seq": 2, "ts": "2026-07-31T08:20:43.000+00:00", "type": EventType.AGENT_INTENT,
     "actor": "LAL", "payload": {"targets": "['curryst01']", "reason": "win now"}},
    {"seq": 3, "ts": "2026-07-31T08:20:43.500+00:00",
     "type": EventType.INTENT_UNSATISFIABLE, "actor": "LAL",
     "payload": {"constraint": "No legal package exists (SALARY_MATCH)."}},
    {"seq": 4, "ts": "2026-07-31T08:23:55.000+00:00", "type": EventType.AGENT_SELECTED,
     "actor": "LAL", "payload": {"declined": "True", "reason": "thin market"}},
    {"seq": 5, "ts": "2026-07-31T08:23:55.900+00:00", "type": EventType.RUN_FINISHED,
     "actor": "system", "payload": {"verdict": "no_trade", "declined_all": "True"}},
]


@pytest.fixture
def feed():
    return build_feed(EVENTS, run_id="test-run")


class TestLimitationsAlwaysPresent:
    def test_limitations_always_present_in_the_terminal_report(self, feed):
        # Normalised because the terminal renderer wraps to a width; the
        # guarantee is that the text is present, not that it is on one line.
        rendered = " ".join(build_report("test-run", {"branch": feed}).render().split())
        for item in LIMITATIONS:
            assert " ".join(item.split()) in rendered

    def test_limitations_always_present_in_html(self, feed):
        page = render_html("t", {"branch": feed})
        for item in LIMITATIONS:
            assert H.escape(item, quote=True) in page

    def test_limitations_survive_when_the_model_says_nothing(self, feed):
        """An empty or failed model response must not take the block with it."""
        report = Report(run_id="r", branches={"b": BranchSummary(what_happened="")})
        rendered = " ".join(report.render().split())
        for item in LIMITATIONS:
            assert " ".join(item.split()) in rendered

    def test_the_precision_figure_is_in_the_limitations(self):
        assert any("1 in 421" in item for item in LIMITATIONS)

    def test_the_precision_figure_reaches_the_html_headline(self, feed):
        assert "421" in render_html("t", {"branch": feed})


class TestClaimFilter:
    @pytest.mark.parametrize(
        "sentence",
        [
            "The Lakers will sign LeBron James next summer.",
            "This predicts a Philadelphia move.",
            "Golden State is a better team than Miami.",
            "Trading for Curry is the best move available.",
            "They should acquire a stretch big.",
            "The sim is expected to land him in Philadelphia.",
        ],
    )
    def test_forbidden_claims_are_dropped(self, sentence):
        kept, dropped = filter_claims(sentence)
        assert dropped == [sentence]
        assert kept == ""

    @pytest.mark.parametrize(
        "sentence",
        [
            "The Lakers proposed a trade for Gary Payton II.",
            "The solver returned no legal package, so the intent was refused.",
            "Miami declined every package it was shown.",
        ],
    )
    def test_descriptions_of_what_happened_survive(self, sentence):
        kept, dropped = filter_claims(sentence)
        assert kept == sentence
        assert dropped == []

    def test_dropped_sentences_are_reported_not_hidden(self):
        report = Report(
            run_id="r",
            branches={"b": BranchSummary(what_happened="ok")},
            dropped=["The Lakers will win the title."],
        )
        rendered = report.render()
        assert "removed by the claim filter" in rendered
        assert "The Lakers will win the title." in rendered

    def test_every_forbidden_pattern_catches_something(self):
        """A pattern that matches nothing is a pattern that has rotted."""
        corpus = [
            "The team will sign him.",
            "This is a forecast of the market.",
            "It proves that Miami is ahead.",
            "Denver is stronger than Utah.",
            "That is the optimal trade.",
            "They should pursue a wing.",
        ]
        for pattern in FORBIDDEN:
            assert any(pattern.search(s) for s in corpus), f"{pattern.pattern} matches nothing"


class TestRefusalsAreShown:
    def test_refusals_are_marked_notable(self, feed):
        kinds = {e.kind for e in feed.refusals}
        assert EventType.INTENT_UNSATISFIABLE in kinds

    def test_a_decline_counts_as_a_refusal(self, feed):
        assert any(e.kind == EventType.AGENT_SELECTED for e in feed.refusals)

    def test_refusals_are_styled_in_html(self, feed):
        assert 'class="refuse"' in render_html("t", {"branch": feed})

    def test_the_feed_renders_the_agents_own_words(self, feed):
        assert any("thin market" in e.reasoning for e in feed.entries)


class TestCounterfactualIsMarked:
    def test_counterfactual_branch_is_labelled_unfalsifiable(self, feed):
        page = render_html("t", {"actual": feed, "cf": feed}, unfalsifiable=("cf",))
        assert "unfalsifiable" in page
        assert "never scored" in page

    def test_the_terminal_report_marks_it_too(self, feed):
        rendered = build_report(
            "r", {"actual": feed, "cf": feed}, unfalsifiable=("cf",)
        ).render()
        assert "COUNTERFACTUAL" in rendered
        assert "UNFALSIFIABLE" in rendered


class TestTheBoundaryHolds:
    """The same rule as every other agent: no salaries, ever."""

    def test_money_questions_are_routed_away_from_the_model(self):
        for question in [
            "how much cap room did you have?",
            "what was the salary matching problem?",
            "could you afford him?",
            "how many millions?",
        ]:
            assert looks_financial(question)

    def test_a_financial_answer_comes_from_the_solver_record(self):
        answer = financial_answer("how much cap room?", EVENTS)
        assert "solver record" in answer.source
        assert "154,856,190" in answer.text

    def test_a_financial_answer_says_so_when_the_run_has_no_figure(self):
        answer = financial_answer("how much cap room?", [EVENTS[0]])
        assert "nothing found" in answer.source
        assert "estimate" in answer.text

    def test_the_option_set_shown_to_the_model_carries_no_salary(self):
        events = EVENTS + [{
            "seq": 6, "ts": "", "type": EventType.PROPOSAL_ASSEMBLED, "actor": "LAL",
            "payload": {"players": json.dumps(
                [{"id": "woodch01", "salary": 3036040, "from": "LAL"}]
            )},
        }]
        options = options_shown(events)
        assert options
        for option in options:
            assert "3036040" not in option
            assert "3,036,040" not in option

    def test_the_report_prompt_carries_no_salary(self, feed):
        digest = feed_digest(feed)
        assert "154856190" not in digest
        assert "$" not in digest


class TestHtmlIsSelfContained:
    def test_no_external_requests(self, feed):
        page = render_html("t", {"branch": feed})
        assert "http://" not in page
        assert "https://" not in page

    def test_untrusted_event_text_is_escaped(self):
        hostile = [dict(EVENTS[2])]
        hostile[0]["payload"] = {
            "targets": "['x']",
            "reason": "<script>alert('xss')</script>",
        }
        page = render_html("t", {"b": build_feed(hostile)})
        assert "<script>alert" not in page
        assert "&lt;script&gt;" in page


class TestEvidenceOnTheSurface:
    """The news layer's one honest claim, rendered and enforced.

    Not that it predicts - that every input is dated, sourced, anchored, and
    on the correct side of the freeze. Interest is displayed as an input, the
    branch fork shows which commitments fired where, and POST rows never reach
    a surface that renders inputs.
    """

    @pytest.fixture(scope="class")
    def ledger(self):
        from mironba.report.evidence_view import load_scenario_ledger

        ledger = load_scenario_ledger("lebron-2026")
        if ledger is None:
            pytest.skip("lebron-2026 ledger not present")
        return ledger

    def test_known_rows_are_pre_dated_sourced_anchored(self, ledger):
        from mironba.report.evidence_view import known_at_freeze

        rows = known_at_freeze(ledger)
        assert rows, "no PRE interest rows rendered"
        for row in rows:
            assert row["date"] <= "2026-07-06"
            assert row["source"] and row["url"].startswith("http")
            assert row["anchors"], f"{row['id']} rendered without its anchor"

    def test_post_interest_never_reaches_the_surface(self, ledger):
        """LBJ-06's narrowing is the answer; the surface renders inputs."""
        from mironba.report.evidence_view import known_at_freeze

        assert all(r["id"] not in ("RI-07", "RI-08", "RI-09")
                   for r in known_at_freeze(ledger))

    def test_the_declared_branch_rule_is_the_declared_rule(self):
        from mironba.report.evidence_view import condition_fires_in

        assert condition_fires_in("IF James signs with Golden State",
                                  "signs_with_blocker")
        assert not condition_fires_in("IF James signs with Golden State",
                                      "signs_elsewhere")
        assert condition_fires_in("IF James signs elsewhere", "signs_elsewhere")

    def test_the_branch_fork_shows_fired_and_dormant(self, ledger):
        from mironba.report.evidence_view import branch_conditionals

        blocker = branch_conditionals(ledger, "signs_with_blocker")
        actual = branch_conditionals(ledger, "signs_elsewhere")
        assert any(c["fired"] for c in blocker)
        assert any(not c["fired"] for c in blocker)
        fired_map = {c["id"]: c["fired"] for c in blocker}
        assert all(fired_map[c["id"]] != c["fired"] for c in actual), (
            "a conditional fired in both branches - the fork is not a fork"
        )

    def test_html_marks_interest_as_input_with_provenance(self, ledger):
        from mironba.report.evidence_view import known_at_freeze  # noqa: F401
        from mironba.report.html import render_html
        from mironba.report.timeline import build_feed

        feed = build_feed([{"seq": 0, "ts": "", "type": "run.started",
                            "actor": "system", "payload": {}}])
        page = render_html("t", {"signs_elsewhere": feed,
                                 "signs_with_blocker": feed},
                           unfalsifiable=("signs_with_blocker",), ledger=ledger)
        assert "Known at the freeze" in page
        assert "retired as a scored metric" in page
        assert "FIRED in this branch" in page and "did not fire here" in page
        assert page.count("sports.yahoo.com") >= 2
        assert "RI-07" not in page
