"""Values used as path components, enumerated, and crashes kept out of verdicts.

A scenario id spent a day as an int. `id: 2026` written unquoted parsed back
as a number, `EVIDENCE_ROOT / self.id` raised "unsupported operand type(s)
for /: 'WindowsPath' and 'int'", and a blanket `except Exception` reported it
as "drafted yaml would not load as a scenario" - a crash dressed as a verdict,
which sent the reader to inspect their sentence instead of the path join.

The same value was fine at every f-string join, because formatting takes
anything. That is what makes this a class rather than an incident: it fails
only where the value is passed bare.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mironba.world.paths import (PATH_COMPONENT_SITES, PathComponentError,
                                 as_component)

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "mironba"
PATHY = ("root", "runs", "dir", "snapshots", "package", "path", "archive")


def _bare_joins() -> set:
    """Every `<pathish> / <name>` in the package, name not a literal."""
    found = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = path.relative_to(PACKAGE).as_posix()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.BinOp)
                    and isinstance(node.op, ast.Div)):
                continue
            left = getattr(node.left, "id", "") or getattr(
                node.left, "attr", "")
            if not any(k in left.lower() for k in PATHY):
                continue
            right = node.right
            if isinstance(right, (ast.Constant, ast.JoinedStr)):
                continue
            name = getattr(right, "id", "") or getattr(right, "attr", "")
            if name:
                found.add((rel, name))
    return found


class TestEveryBareJoinIsAccountedFor:
    def test_no_unregistered_bare_path_join_exists(self):
        """A new `Path / value` join must say how the value is a string.
        Adding one without an entry fails here - the same fence as the
        writer registry and the findings dispositions."""
        unregistered = _bare_joins() - set(PATH_COMPONENT_SITES)
        assert unregistered == set(), (
            f"bare path joins with no declared guarantee: "
            f"{sorted(unregistered)}")

    def test_the_registry_names_no_join_that_is_gone(self):
        stale = set(PATH_COMPONENT_SITES) - _bare_joins()
        assert stale == set(), f"registry names vanished joins: {sorted(stale)}"

    def test_every_site_states_how_the_value_is_a_string(self):
        for site, why in PATH_COMPONENT_SITES.items():
            assert len(why) > 30, f"{site}: reason too thin"


class TestTheGuardRefusesRatherThanCoerces:
    def test_it_is_catchable_as_a_value_error(self):
        """Every caller guards with `except ValueError`. A TypeError here
        would sail past those handlers and surface as a 500 - an internal
        failure presented as something it is not, which is the swap this
        whole change is about."""
        assert issubclass(PathComponentError, ValueError)

    def test_a_number_is_refused_not_silently_stringified(self):
        """Coercing would make 2026 and "2026" the same directory, which is
        how a numeric id gets a second life instead of being fixed."""
        with pytest.raises(PathComponentError, match="must be a string"):
            as_component(2026, "scenario id")

    def test_empty_and_padded_values_are_refused(self):
        for bad in ("", " x", "x "):
            with pytest.raises(PathComponentError):
                as_component(bad, "scenario id")

    def test_a_separator_or_dotdot_is_refused(self):
        for bad in ("..", "a/b", "a\\b", "."):
            with pytest.raises(PathComponentError):
                as_component(bad, "scenario id")

    def test_a_good_component_passes_through_unchanged(self):
        assert as_component("curry-to-lakers-2026", "x") == \
            "curry-to-lakers-2026"


class TestTheNumericIdIsCaughtAsAVerdict:
    def test_a_numeric_id_raises_a_scenario_error_not_a_typeerror(self):
        """The original crash, now a judgement with the fix in the message."""
        import yaml

        from mironba.world.scenario import ScenarioError, scenario_from_raw

        raw = yaml.safe_load(
            (ROOT / "configs" / "branch" / "curry-lakers-2026.yaml")
            .read_text(encoding="utf-8"))
        raw["id"] = 2026
        with pytest.raises(ScenarioError, match="must be a string"):
            scenario_from_raw(raw)

    def test_the_writer_quotes_the_id_so_yaml_cannot_retype_it(self):
        from mironba.world.authoring import Draft, scenario_yaml

        draft = Draft(sentence="x", kind="stipulated", event="signing",
                      seed_date="2026-07-06",
                      moves=[{"player_name": "LeBron James",
                              "from_team": "", "to_team": "GSW"}],
                      resolved={"LeBron James": "jamesle01"},
                      player_names=["LeBron James"], team_codes=["GSW"])
        text = scenario_yaml(draft, "2026")
        assert 'id: "2026"' in text
        import yaml

        assert isinstance(yaml.safe_load(text)["id"], str)


class TestACrashIsNotAVerdict:
    def test_an_unexpected_exception_does_not_surface_as_validation(self):
        """THE test the brief asks for. A loader that blows up has judged
        nothing, and saying "would not load as a scenario" claims it did."""
        import mironba.world.scenario as scenario_mod
        from mironba.world.authoring import (AuthoringCrash, AuthoringError,
                                             Draft, write_scenario)

        draft = Draft(sentence="x", kind="stipulated", event="signing",
                      seed_date="2026-07-06",
                      moves=[{"player_name": "LeBron James",
                              "from_team": "", "to_team": "GSW"}],
                      resolved={"LeBron James": "jamesle01"},
                      player_names=["LeBron James"], team_codes=["GSW"])
        original = scenario_mod.scenario_from_raw

        def boom(_raw):
            raise ZeroDivisionError("the loader fell over")

        scenario_mod.scenario_from_raw = boom
        try:
            with pytest.raises(AuthoringCrash) as caught:
                write_scenario(draft, "crash-probe-2026", confirmed=True,
                               config_dir=ROOT / "configs" / "branch")
        finally:
            scenario_mod.scenario_from_raw = original

        message = str(caught.value)
        assert "ZeroDivisionError" in message
        assert "crashed" in message
        assert "NOT a verdict" in message
        assert "would not load as a scenario" not in message
        assert caught.value.traceback, "the traceback must be available"
        assert not isinstance(caught.value, AuthoringError), (
            "a crash must not be catchable as a validation rejection")
        assert not (ROOT / "configs" / "branch"
                    / "crash-probe-2026.yaml").exists()

    def test_a_real_validation_failure_still_reads_as_a_rejection(self):
        """The other half: a genuine ScenarioError must still say what
        would resolve it, or this change has traded one silence for
        another."""
        import mironba.world.scenario as scenario_mod
        from mironba.world.authoring import (AuthoringError, Draft,
                                             write_scenario)
        from mironba.world.scenario import ScenarioError

        draft = Draft(sentence="x", kind="stipulated", event="signing",
                      seed_date="2026-07-06",
                      moves=[{"player_name": "LeBron James",
                              "from_team": "", "to_team": "GSW"}],
                      resolved={"LeBron James": "jamesle01"},
                      player_names=["LeBron James"], team_codes=["GSW"])
        original = scenario_mod.scenario_from_raw

        def refuse(_raw):
            raise ScenarioError("subjects required")

        scenario_mod.scenario_from_raw = refuse
        try:
            with pytest.raises(AuthoringError) as caught:
                write_scenario(draft, "reject-probe-2026", confirmed=True,
                               config_dir=ROOT / "configs" / "branch")
        finally:
            scenario_mod.scenario_from_raw = original
        assert "would not load as a scenario" in str(caught.value)
        assert "subjects required" in str(caught.value)
