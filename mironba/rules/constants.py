"""League-fact constants for the 2023 CBA.

Everything here is a *league fact*, not a modelling choice. Modelling choices
belong in ``models/``. Each field carries a provenance entry in ``PROVENANCE``
recording where the number came from and how much we trust it — the charter's
reproducibility non-negotiable applies to inputs, not just outputs.

All money is **integer dollars**. Never floats: 125% of an odd salary must be
reproducible to the cent across machines, and binary floats are not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Confidence = Literal["verified", "derived", "unverified"]


# --------------------------------------------------------------------------
# Rules that do not vary by season
# --------------------------------------------------------------------------

#: The "+$250K" cushion on salary-matching. Raised from $100K by the 2023 CBA.
TRADE_CUSHION = 250_000

#: Matching percentage for large outgoing salaries, teams below the first apron.
STANDARD_MATCH_PCT = 125

#: Matching percentage for small outgoing salaries, teams below the first apron.
SMALL_SALARY_MATCH_PCT = 200

#: Roster limits. The 2023 CBA moved two-way slots from 2 to 3.
MAX_STANDARD_ROSTER = 15
MIN_STANDARD_ROSTER = 14
MAX_TWO_WAY = 3

#: A player acquired by trade may not be *aggregated* with another player for
#: this many days after the acquisition.
AGGREGATION_WINDOW_DAYS = 60

#: A newly signed free agent may not be traded until the later of this many
#: days after signing, or ``SIGNING_TRADE_RESTRICTION_DATE`` of that season.
SIGNING_RESTRICTION_DAYS = 90
SIGNING_RESTRICTION_MONTH_DAY = (12, 15)

#: Stepien rule: a team may not be left without a first-round pick in two
#: consecutive future drafts. Evaluated over this horizon.
STEPIEN_HORIZON_YEARS = 7

#: Smallest cash consideration that may appear in a trade.
MIN_CASH_IN_TRADE = 110_000

#: Longest contract a player may be *acquired by trade* on under the minimum
#: salary exception. Longer deals do not qualify.
MAX_MINIMUM_EXCEPTION_CONTRACT_YEARS = 2

#: Minimum salary by years of NBA service, per season. Key 10 means "10 or
#: more". Sourced from Hoops Rumors' annual minimum-salary tables; see
#: PROVENANCE["minimum_salary_scale"].
MINIMUM_SALARY_SCALE: dict[str, dict[int, int]] = {
    "2023-24": {
        0: 1_119_563, 1: 1_801_769, 2: 2_019_706, 3: 2_092_354, 4: 2_165_000,
        5: 2_346_614, 6: 2_528_233, 7: 2_709_849, 8: 2_891_467, 9: 2_905_861,
        10: 3_196_448,
    },
    "2024-25": {
        0: 1_157_153, 1: 1_862_265, 2: 2_087_519, 3: 2_162_606, 4: 2_237_691,
        5: 2_425_403, 6: 2_613_120, 7: 2_800_834, 8: 2_988_550, 9: 3_003_427,
        10: 3_303_771,
    },
    "2025-26": {
        0: 1_272_870, 1: 2_048_494, 2: 2_296_274, 3: 2_378_870, 4: 2_461_463,
        5: 2_667_947, 6: 2_874_436, 7: 3_080_921, 8: 3_287_409, 9: 3_303_774,
        10: 3_634_153,
    },
    "2026-27": {
        0: 1_357_763, 1: 2_185_116, 2: 2_449_421, 3: 2_537_526, 4: 2_625_627,
        5: 2_845_883, 6: 3_066_143, 7: 3_286_399, 8: 3_506_659, 9: 3_524_115,
        10: 3_876_529,
    },
}


# --------------------------------------------------------------------------
# Per-season cap environment
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapEnvironment:
    """The cap landscape for one league year.

    ``apron_match_pct`` is the single most load-bearing season-varying number:
    the 2023 CBA capped apron teams at 110% of outgoing salary for 2023/24
    only, then tightened it to 100% from 2024/25 onward. Hardcoding 125%
    everywhere — the pre-2023 intuition — silently approves illegal trades.
    """

    season: str
    salary_cap: int
    tax_level: int
    first_apron: int
    second_apron: int
    minimum_team_salary: int
    #: Middle-tier trade buffer ("expanded traded player exception"). Scales
    #: with the cap each season.
    expanded_tpe: int
    #: Max share of outgoing salary an *apron* team may take back, in percent.
    apron_match_pct: int
    #: Max cash a team may send across a league year, and separately the max it
    #: may receive. The two limits are independent, not a single pool.
    #:
    #: This is a *limit*, and it is not what stops a second-apron team from
    #: sending cash — that is an outright prohibition, enforced as its own rule
    #: in the validator. Setting this to 0 would be the wrong way to express a
    #: ban, because a ban is not "a limit of zero": it must survive any future
    #: change to this number, and it must produce its own finding so a GM agent
    #: learns the right lesson.
    cash_limit: int
    #: Non-taxpayer mid-level exception — referenced only by the buyout rule.
    non_taxpayer_mle: int
    #: The remaining signing exceptions, as *first-year* amounts. How long each
    #: may run and what raise it permits are rules rather than money, so they
    #: live in ``rules/signing.py``.
    taxpayer_mle: int = 0
    room_exception: int = 0
    bi_annual_exception: int = 0

    @property
    def start_year(self) -> int:
        """Calendar year the season starts in: ``"2025-26"`` -> ``2025``."""
        return int(self.season[:4])


_ENVIRONMENTS: dict[str, CapEnvironment] = {
    "2023-24": CapEnvironment(
        season="2023-24",
        salary_cap=136_021_000,
        tax_level=165_294_000,
        first_apron=172_346_000,
        second_apron=182_794_000,
        minimum_team_salary=122_419_000,
        expanded_tpe=7_500_000,
        apron_match_pct=110,
        cash_limit=7_005_000,
        non_taxpayer_mle=12_405_000,
        taxpayer_mle=5_000_000,
        room_exception=7_723_000,
        bi_annual_exception=4_516_000,
    ),
    "2024-25": CapEnvironment(
        season="2024-25",
        salary_cap=140_588_000,
        tax_level=170_814_000,
        first_apron=178_132_000,
        second_apron=188_931_000,
        minimum_team_salary=126_529_000,
        expanded_tpe=7_752_000,
        apron_match_pct=100,
        cash_limit=7_240_000,
        non_taxpayer_mle=12_822_000,
        taxpayer_mle=5_168_000,
        room_exception=7_983_000,
        bi_annual_exception=4_667_000,
    ),
    "2025-26": CapEnvironment(
        season="2025-26",
        salary_cap=154_647_000,
        tax_level=187_895_000,
        first_apron=195_945_000,
        second_apron=207_824_000,
        minimum_team_salary=139_182_000,
        expanded_tpe=8_527_000,
        apron_match_pct=100,
        cash_limit=7_964_000,
        non_taxpayer_mle=14_104_000,
        taxpayer_mle=5_685_000,
        room_exception=8_781_000,
        bi_annual_exception=5_134_000,
    ),
    "2026-27": CapEnvironment(
        season="2026-27",
        salary_cap=164_961_000,
        tax_level=200_428_000,
        first_apron=209_015_000,
        second_apron=221_686_000,
        minimum_team_salary=148_465_000,
        expanded_tpe=9_096_000,
        apron_match_pct=100,
        cash_limit=8_497_000,
        non_taxpayer_mle=15_044_000,
        taxpayer_mle=6_064_000,
        room_exception=9_366_000,
        bi_annual_exception=5_477_000,
    ),
}


#: Where each number came from, and how much to trust it.
#:
#: "verified"   — confirmed against a primary or high-quality secondary source.
#: "derived"    — computed from a verified number by a rule we also verified
#:                (e.g. minimum team salary is 90% of the cap; the expanded TPE
#:                scales with the cap).
#: "unverified" — recalled, not confirmed. Treat as a placeholder.
PROVENANCE: dict[str, tuple[Confidence, str]] = {
    "salary_cap": ("verified", "NBA PR releases; Hoops Rumors cap/tax announcements"),
    "tax_level": ("verified", "NBA PR releases"),
    "first_apron": ("verified", "NBA PR releases; Hoops Rumors 'Tax Aprons' glossary"),
    "second_apron": ("verified", "NBA PR releases; Hoops Rumors 'Tax Aprons' glossary"),
    "minimum_team_salary": (
        "derived",
        "90% of the salary cap. Cross-checks exactly against the announced "
        "2026-27 figure of $148.465M (= 0.90 x $164.961M).",
    ),
    "expanded_tpe": (
        "verified",
        "Published directly for 2023-24 ($7,500,000), 2025-26 ($8,527,000) and "
        "2026-27 ($9,096,000, The CBA Guide's current bracket table). 2024-25 "
        "($7,752,000) is the one season interpolated cap-proportionally, and "
        "the chain through it lands on both published neighbours to the "
        "nearest $1K.",
    ),
    "apron_match_pct": (
        "verified",
        "110% for apron teams in 2023-24 only; 100% from 2024-25 onward "
        "(Hoops Rumors salary-matching rules; 'Tax Aprons' glossary).",
    ),
    "cash_limit": (
        "verified",
        "Sourced per season: $7,005,000 (2023-24), $7,240,000 (2024-25), "
        "$7,964,000 (2025-26), $8,497,000 (2026-27) — Hoops Rumors' annual "
        "'Cash Sent, Received In NBA Trades' trackers. Every season lands on "
        "5.15% of that season's cap, which is the cross-check. An earlier "
        "revision of this file assumed the limit tracked the expanded TPE; it "
        "does not, and every figure was wrong by $250K-$600K.",
    ),
    "minimum_salary_scale": (
        "derived",
        "2023-24, 2024-25 and 2025-26 come from Hoops Rumors annual "
        "minimum-salary tables, scraped in full rather than summarised. "
        "2026-27 is DERIVED: each season's scale is the previous season's "
        "scaled by the cap ratio. That is not an approximation - applied to "
        "2023-24 it reproduces 2024-25 exactly, and applied to 2024-25 it "
        "reproduces 2025-26 exactly, at all eleven service tiers, to the "
        "dollar. Cross-checked against two real 2026-27 contracts: the 10+ "
        "tier lands on $3,876,529, which is LeBron James's actual "
        "Philadelphia salary, and the 2-year tier on $2,449,421, which is "
        "Charles Bassey's actual Golden State salary. Hoops Rumors "
        "independently describes the 2026-27 veteran minimum as 'nearly "
        "$3.88MM' and the rookie minimum as 'about $1.36MM' (derived: "
        "$1,357,763).",
    ),
    "taxpayer_mle": (
        "verified",
        "2026-27 ($6,064,000) from Hoops Rumors, 'Values Of 2026/27 Mid-Level, "
        "Bi-Annual Exceptions', retrieved 2026-07-31. Earlier seasons carry the "
        "published figures; all four land on 3.676% of the cap to within "
        "0.0002 percentage points, which is the cross-check.",
    ),
    "room_exception": (
        "verified",
        "2026-27 ($9,366,000) from the same Hoops Rumors table, retrieved "
        "2026-07-31, which also states the 5.678%-of-cap relationship. All four "
        "seasons land on 5.678% to within 0.0006 points.",
    ),
    "bi_annual_exception": (
        "verified",
        "2026-27 ($5,477,000) from the same Hoops Rumors table, retrieved "
        "2026-07-31, which states the 3.32%-of-cap relationship. All four "
        "seasons land on 3.320% to within 0.0006 points.",
    ),
    "non_taxpayer_mle": (
        "verified",
        "NBA PR cap releases: $12,405,000 (2023-24), $12,822,000 (2024-25), "
        "$14,104,000 (2025-26), $15,044,000 (2026-27). The 2026-27 figure is "
        "9.12% of the cap, the formula Hoops Rumors publishes.",
    ),
}


# --------------------------------------------------------------------------
# Contested readings
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContestedRule:
    """A rule where sources disagree and we had to choose.

    Recorded rather than quietly resolved, because the choice changes verdicts
    and a future reader deserves to know it was a choice.
    """

    question: str
    adopted: str
    rationale: str
    alternative: str
    impact_if_wrong: str
    sources: tuple[str, ...]


CONTESTED: dict[str, ContestedRule] = {
    "lower_bracket_boundary": ContestedRule(
        question=(
            "Where does the 200% + $250K salary-matching bracket end for a team "
            "below the first apron — at $7,250,000 or $7,500,000 (2023-24)?"
        ),
        adopted="$7,250,000 — the crossover, which is what the median formulation yields.",
        rationale=(
            "The CBA constructs these brackets so that adjacent formulas meet "
            "exactly at their crossover points, which makes the bracket table "
            "identical to the median of the three formulas. Four published "
            "boundaries across two CBAs confirm it and none contradict it:\n"
            "  2023 CBA, 2026-27 (expanded TPE $9,096,000): published bracket\n"
            "    edges $8,846,000 and $35,384,000. Solving 2X + 250K = X + "
            "9,096,000 gives exactly $8,846,000; solving X + 9,096,000 = "
            "1.25X + 250K gives exactly $35,384,000.\n"
            "  2017 CBA (buffer $5,000,000, 175%/125%, $100K cushion): "
            "published edges $6,533,333 and $19,600,000, both likewise exact "
            "crossovers.\n"
            "Under that construction the 2023-24 lower edge is $7,250,000, not "
            "$7,500,000. The '$7.5M' figure that appears in aggregator "
            "coverage is the expanded-TPE amount reused as a round-number "
            "label; Sports Business Classroom published a correction moving it "
            "to $7.25M after the final CBA text was released.\n"
            "Caveat: we did not read Article VII verbatim — the CBA PDF timed "
            "out on fetch. This rests on exact arithmetic agreement at four "
            "published boundaries plus that correction, not on primary text."
        ),
        alternative="$7,500,000, as stated in Hoops Rumors' 2023-24 bracket table.",
        impact_if_wrong=(
            "We would be too strict in the narrow band of outgoing salaries "
            "between $7,250,001 and $7,500,000, and only there. At $7,490,000 "
            "outgoing in 2023-24 we allow $14,990,000 where the other reading "
            "allows $15,230,000 — a $240,000 false rejection. Errors run "
            "toward rejecting legal trades, never toward approving illegal "
            "ones, which is the safe direction for a gate an LLM proposes "
            "into. No fixture currently sits in the band."
        ),
        sources=(
            "https://cbaguide.com/transactions/trades/tpe/",
            "https://sportsbusinessclassroom.com/understanding-trade-matching-in-the-new-collective-bargaining-agreement/",
            "https://www.hoopsrumors.com/2023/09/salary-matching-rules-for-trades-during-2023-24-season.html",
        ),
    ),
}


def environment_for(season: str) -> CapEnvironment:
    """Return the cap environment for ``season`` (e.g. ``"2025-26"``)."""
    try:
        return _ENVIRONMENTS[season]
    except KeyError:
        known = ", ".join(sorted(_ENVIRONMENTS))
        raise KeyError(f"no cap environment for season {season!r}; known: {known}") from None


def known_seasons() -> list[str]:
    """Seasons with a defined cap environment, chronologically."""
    return sorted(_ENVIRONMENTS)
