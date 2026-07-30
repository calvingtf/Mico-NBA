-- Micro NBA world database.
--
-- Money is INTEGER dollars throughout. Dates are ISO-8601 TEXT ('YYYY-MM-DD')
-- so SQLite's lexicographic comparison is also chronological comparison.
--
-- Everything is scoped by snapshot_id. A simulation reads exactly one snapshot
-- and records its id in the run manifest, so a result can always be replayed
-- against the data that produced it.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id   TEXT PRIMARY KEY,
    -- Date the underlying data reflects, not the date it was loaded.
    as_of_date    TEXT NOT NULL,
    season        TEXT NOT NULL,
    source        TEXT NOT NULL,
    loaded_at     TEXT NOT NULL,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS teams (
    team_id       TEXT NOT NULL,
    snapshot_id   TEXT NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    city          TEXT NOT NULL,
    conference    TEXT NOT NULL CHECK (conference IN ('East', 'West')),
    division      TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, team_id)
);

CREATE TABLE IF NOT EXISTS players (
    player_id     TEXT NOT NULL,
    snapshot_id   TEXT NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    position      TEXT,
    birth_date    TEXT,
    PRIMARY KEY (snapshot_id, player_id)
);

-- One row per player-season cap hit. `salary` is what the trade validator
-- matches on; `guaranteed` drives buyout and waiver logic later.
CREATE TABLE IF NOT EXISTS contracts (
    snapshot_id           TEXT NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    player_id             TEXT NOT NULL,
    team_id               TEXT NOT NULL,
    season                TEXT NOT NULL,
    salary                INTEGER NOT NULL CHECK (salary >= 0),
    guaranteed            INTEGER NOT NULL DEFAULT 0,
    contract_type         TEXT NOT NULL DEFAULT 'standard'
                          CHECK (contract_type IN ('standard', 'two_way', 'exhibit_10', 'dead')),
    signed_on             TEXT,
    acquired_via_trade_on TEXT,
    trade_restricted_until TEXT,
    no_trade_clause       INTEGER NOT NULL DEFAULT 0 CHECK (no_trade_clause IN (0, 1)),
    -- Base-year compensation: outgoing match value differs from the cap hit.
    outgoing_match_value  INTEGER,
    -- BYC preconditions. Defaults to 'unknown' rather than 'not_re_signed'
    -- because a snapshot that omits the column has not told us anything, and
    -- the validator must say so rather than assume the convenient answer.
    re_sign_status        TEXT NOT NULL DEFAULT 'unknown'
                          CHECK (re_sign_status IN
                                 ('not_re_signed', 're_signed_bird', 'unknown')),
    previous_salary       INTEGER,
    PRIMARY KEY (snapshot_id, player_id, season),
    FOREIGN KEY (snapshot_id, player_id) REFERENCES players(snapshot_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_contracts_team
    ON contracts (snapshot_id, season, team_id);

-- Salary a team carries that is not attached to a rostered player.
CREATE TABLE IF NOT EXISTS dead_money (
    snapshot_id   TEXT NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    team_id       TEXT NOT NULL,
    season        TEXT NOT NULL,
    amount        INTEGER NOT NULL,
    description   TEXT,
    PRIMARY KEY (snapshot_id, team_id, season, description)
);

CREATE TABLE IF NOT EXISTS trade_exceptions (
    snapshot_id     TEXT NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    team_id         TEXT NOT NULL,
    label           TEXT NOT NULL,
    amount          INTEGER NOT NULL,
    created_season  TEXT NOT NULL,
    expires_on      TEXT,
    from_sign_and_trade INTEGER NOT NULL DEFAULT 0
                        CHECK (from_sign_and_trade IN (0, 1)),
    PRIMARY KEY (snapshot_id, team_id, label)
);

CREATE TABLE IF NOT EXISTS draft_picks (
    snapshot_id   TEXT NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    -- The team whose pick it originally is.
    origin_team   TEXT NOT NULL,
    -- The team that currently controls it.
    owner_team    TEXT NOT NULL,
    draft_year    INTEGER NOT NULL,
    round         INTEGER NOT NULL CHECK (round IN (1, 2)),
    protection    TEXT,
    PRIMARY KEY (snapshot_id, origin_team, draft_year, round)
);

CREATE TABLE IF NOT EXISTS player_stats (
    snapshot_id   TEXT NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    player_id     TEXT NOT NULL,
    season        TEXT NOT NULL,
    games         INTEGER,
    minutes       REAL,
    points        REAL,
    rebounds      REAL,
    assists       REAL,
    -- Single-number impact estimates feed models/value.py.
    box_plus_minus REAL,
    win_shares     REAL,
    PRIMARY KEY (snapshot_id, player_id, season)
);

-- Reproducibility non-negotiable: no manifest, no result.
CREATE TABLE IF NOT EXISTS run_manifests (
    run_id             TEXT PRIMARY KEY,
    snapshot_id        TEXT NOT NULL REFERENCES snapshots(snapshot_id),
    scenario           TEXT NOT NULL,
    seed               INTEGER NOT NULL,
    model_id           TEXT,
    quantization       TEXT,
    temperature        REAL,
    prompt_template_hash TEXT,
    code_version       TEXT,
    started_at         TEXT NOT NULL,
    finished_at        TEXT
);
