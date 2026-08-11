# Architecture

The two paths through the system, the one approver, and how the pieces fit.

[← back to the README](../README.md)

---

## Architecture: two paths, one approver

An LLM may *propose*; only `rules/` may *approve*. The model names players and
selects from a list — it never emits a package, never states terms, and
nothing it says reaches world state without passing the validator.

```mermaid
flowchart LR
    subgraph DET["Deterministic path — no LLM anywhere in it"]
        league["sim/league.py<br/>30-team reaction"]
        cascade["sim/cascade.py<br/>follow-on trades"]
        branch["sim/branch.py<br/>branch planner"]
        deadline["sim/deadline.py<br/>deadline backtest"]
        stip["sim/stipulated.py<br/>stipulated seeds"]
    end
    subgraph LLMP["LLM path — ONE model per tick, not thirty agents"]
        tick["sim/tick.py"]
        gm["agents/gm.py<br/>states INTENT only:<br/>no dollars, no packages"]
        surface["agents/report.py + chat.py<br/>prose over recorded runs,<br/>limitation blocks appended in code"]
    end
    solver["rules/solver.py<br/>deterministic package search<br/>(the ONLY meeting point)"]
    rules["rules/trade_validator.py + cap.py + signing.py<br/>THE ONLY APPROVER"]
    tick --> gm
    gm -->|TradeIntent| solver
    cascade -->|deterministic intent| solver
    solver -->|legal packages only| rules
    league --> rules
    branch --> rules
    deadline --> rules
    stip -->|stipulated trade validated FIRST| rules
```

The two paths meet at the solver and nowhere else. An LLM cannot reach the
validator directly — illegal packages are unrepresentable, not merely
discouraged. Salary-cap math, trade legality and roster construction are
Python; a proposal the rules refuse is refused with findings, including the
project's own stipulated premises.

**If you read only three sections**:
[the boundary finding](#the-boundary-finding-rank-upstream-of-the-constraints) ·
[seven results that weren't](#seven-results-that-werent) ·
[the standing boundary](#the-standing-boundary).
Full map: [measured results](#what-was-measured) ·
[results that weren't](#seven-results-that-werent) ·
[the standing boundary](#the-standing-boundary) ·
[the full measurement ledger](docs/measurements.md) (62 entries — every
number, what it overturned, what changed because of it).

---

## How it is put together

```
                  ┌────────────────────────────────────────┐
  data/           │ Basketball-Reference  ->  contracts    │ scraped, not redistributed
                  │ stats.nba.com         ->  box scores   │ league API, committed
                  └───────────────┬────────────────────────┘
                                  v
  rules/    cap . trade_validator . solver . signing . signing_solver
            -- deterministic, unit-tested. Enumerates every LEGAL option.
                                  |
                  ┌───────────────┴────────────────┐
                  v                                v
  agents/   GM: names a player, an intent,    models/  value -> win_delta
            or an index. Never a salary,               -> delta_error -> compare
            never a package, never terms.              -- refuses to rank
                  |                                       inside its own error
                  └───────────────┬────────────────┘
                                  v
  world/    manifest . events . evidence (PRE|POST freeze) . pending decisions
                                  v
  sim/      tick . branch . league (contention + event-driven scheduler)
                                  v
  eval/     the only code allowed to read the answer
```
