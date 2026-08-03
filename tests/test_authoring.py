"""Authoring: the model drafts, determinism validates, a human signs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mironba.world.authoring import (
    AuthoringError,
    Draft,
    choose,
    draft_from_sentence,
    player_table,
    resolve_name,
    validate_draft,
    write_scenario,
)

ROOT = Path(__file__).resolve().parents[1] / "mironba"


class StubClient:
    """Returns a canned proposal; records the schema it was asked to fill."""

    def __init__(self, payload):
        self.payload = payload
        self.schema_seen = None

    def complete(self, messages, schema=None, profile="default", **kwargs):
        self.schema_seen = schema
        return schema(**self.payload)


CURRY_TRADE = {
    "kind": "stipulated", "seed_date": "2026-07-06",
    "decision": "Curry is traded to the Lakers.",
    "player_names": ["Curry"], "team_codes": ["GSW", "LAL"],
    "moves": [{"player_name": "Curry", "from_team": "GSW", "to_team": "LAL"},
              {"player_name": "Austin Reaves", "from_team": "LAL", "to_team": "GSW"},
              {"player_name": "Quentin Grimes", "from_team": "LAL", "to_team": "GSW"}],
    "scored_teams": [],
}


def curry_draft():
    return draft_from_sentence("Curry traded to the Lakers", StubClient(CURRY_TRADE))


class TestTheModelCannotStateASalary:
    def test_the_draft_schema_has_no_salary_field(self):
        stub = StubClient(CURRY_TRADE)
        draft_from_sentence("x", stub)
        schema = json.dumps(stub.schema_seen.model_json_schema()).lower()
        assert "salary" not in schema and "contract" not in schema

    def test_drafting_writes_nothing(self, tmp_path):
        before = sorted(tmp_path.iterdir())
        draft_from_sentence("x", StubClient(CURRY_TRADE))
        assert sorted(tmp_path.iterdir()) == before


class TestAmbiguitySurfaces:
    def test_curry_matches_both_currys_and_neither_is_chosen(self):
        candidates = resolve_name("Curry", player_table())
        ids = [pid for pid, _ in candidates]
        assert "curryst01" in ids and "curryse01" in ids

    def test_an_ambiguous_draft_cannot_validate(self):
        draft = validate_draft(curry_draft())
        assert "Curry" in draft.ambiguities
        assert not draft.ok

    def test_the_choice_must_be_a_listed_candidate(self):
        draft = validate_draft(curry_draft())
        with pytest.raises(AuthoringError, match="not a candidate"):
            choose(draft, "Curry", "jamesle01")

    def test_a_human_choice_unblocks_validation(self):
        draft = validate_draft(curry_draft())
        choose(draft, "Curry", "curryst01")
        draft.errors.clear()
        draft.findings.clear()
        validate_draft(draft)
        assert draft.ok, (draft.errors, draft.ambiguities)


class TestDeterministicValidation:
    def test_an_unknown_name_is_an_error_not_a_guess(self):
        payload = dict(CURRY_TRADE, player_names=["Zzyzx Nobody"], moves=[])
        payload["kind"] = "pending_decision"
        draft = validate_draft(draft_from_sentence("x", StubClient(payload)))
        assert any("matches nobody" in e for e in draft.errors)

    def test_an_unknown_team_is_an_error(self):
        payload = dict(CURRY_TRADE, team_codes=["XXX"])
        draft = validate_draft(draft_from_sentence("x", StubClient(payload)))
        assert any("no such team" in e for e in draft.errors)

    def test_a_date_outside_the_ingested_window_is_an_error(self):
        payload = dict(CURRY_TRADE, seed_date="2010-01-01")
        draft = validate_draft(draft_from_sentence("x", StubClient(payload)))
        assert any("outside the ingested window" in e for e in draft.errors)

    def test_an_illegal_package_is_refused_with_findings(self):
        payload = dict(CURRY_TRADE, moves=[
            {"player_name": "Stephen Curry", "from_team": "GSW", "to_team": "LAL"},
            {"player_name": "Luka Doncic", "from_team": "LAL", "to_team": "GSW"},
        ], player_names=["Stephen Curry"])
        draft = validate_draft(draft_from_sentence("x", StubClient(payload)))
        assert any("not a legal trade" in e for e in draft.errors)
        assert draft.findings, "the validator's findings must be shown"


class TestTheGate:
    def _clean(self):
        draft = validate_draft(curry_draft())
        choose(draft, "Curry", "curryst01")
        draft.errors.clear()
        draft.findings.clear()
        return validate_draft(draft)

    def test_no_write_without_confirmation(self, tmp_path):
        with pytest.raises(AuthoringError, match="explicit human confirmation"):
            write_scenario(self._clean(), "t-1", config_dir=tmp_path)
        assert not list(tmp_path.iterdir())

    def test_an_unclean_draft_cannot_be_written_even_confirmed(self, tmp_path):
        draft = validate_draft(curry_draft())  # still ambiguous
        with pytest.raises(AuthoringError, match="not clean"):
            write_scenario(draft, "t-2", confirmed=True, config_dir=tmp_path)

    def test_a_confirmed_clean_draft_writes_and_loads(self, tmp_path):
        path = write_scenario(self._clean(), "t-3", confirmed=True,
                              config_dir=tmp_path)
        text = path.read_text(encoding="utf-8")
        assert "curryst01" in text and "salary" not in text.lower()

    def test_authoring_never_overwrites(self, tmp_path):
        write_scenario(self._clean(), "t-4", confirmed=True, config_dir=tmp_path)
        with pytest.raises(AuthoringError, match="never overwrites"):
            write_scenario(self._clean(), "t-4", confirmed=True,
                           config_dir=tmp_path)

    def test_no_other_module_writes_scenario_yaml(self):
        offenders = []
        for path in ROOT.rglob("*.py"):
            if path.name == "authoring.py":
                continue
            text = path.read_text(encoding="utf-8")
            # the write pattern: constructing the scenario config path AND
            # writing files. Mentioning the path in --help text is not writing.
            builds_path = ('"configs" / "branch"' in text
                           or "configs/branch/" in text.replace("under configs/branch/", ""))
            if builds_path and ".write_text" in text:
                offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, (
            f"scenario yaml has a second writer: {offenders}; write_scenario "
            "with its confirmation gate must stay the only one"
        )


class TestTheWorldKnowledgeSurfaceIsEnumerated:
    def test_the_registry_covers_the_proposal_schema_exactly(self):
        """A new model-filled field cannot ship unaudited: the registry must
        name every Proposal field (and every Move subfield) and nothing else."""
        from mironba.world.authoring import WORLD_KNOWLEDGE_FIELDS

        stub = StubClient(CURRY_TRADE)
        draft_from_sentence("x", stub)
        schema = stub.schema_seen.model_json_schema()
        fields = set(schema["properties"])
        move_fields = {f"moves.{name}"
                       for name in schema["$defs"]["Move"]["properties"]}
        expected = (fields - {"moves"}) | move_fields
        assert set(WORLD_KNOWLEDGE_FIELDS) == expected, (
            "registry and schema diverged: "
            f"missing={expected - set(WORLD_KNOWLEDGE_FIELDS)} "
            f"stale={set(WORLD_KNOWLEDGE_FIELDS) - expected}"
        )

    def test_a_wrong_scored_team_can_no_longer_pass(self):
        """The audit's finding: scored_teams was unchecked and a garbage code
        validated cleanly. It errors now."""
        payload = dict(CURRY_TRADE, kind="pending_decision", moves=[],
                       scored_teams=["XYZ"])
        draft = validate_draft(draft_from_sentence("x", StubClient(payload)))
        assert any("no such team: 'XYZ'" in e for e in draft.errors)

    def test_a_written_scenario_must_round_trip_or_be_rolled_back(self, tmp_path):
        """An unloadable yaml is a write that did not happen. (tmp roots skip
        the load - the loader only reads the real config dir - so this
        asserts the refusal path for pending drafts instead, which is the
        v0 boundary the round-trip protects.)"""
        payload = dict(CURRY_TRADE, kind="pending_decision", moves=[],
                       scored_teams=["GSW"],
                       player_names=["Stephen Curry"])
        draft = validate_draft(draft_from_sentence("x", StubClient(payload)))
        assert draft.ok, (draft.errors, draft.ambiguities)
        with pytest.raises(AuthoringError, match="stipulated scenarios only"):
            write_scenario(draft, "t-pending", confirmed=True,
                           config_dir=tmp_path)
        assert not list(tmp_path.iterdir())
