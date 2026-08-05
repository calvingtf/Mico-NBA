# MiroNBA — Project Charter

Multi-agent simulation of counterfactual NBA scenarios. Seed a "what if"
(e.g. *Curry traded to the Lakers*), simulate the league's reaction over a
compressed timeline, and produce a scored, reproducible report.

## Non-negotiables

1. **Deterministic rules stay deterministic.** Salary-cap math, trade legality,
   and roster construction are Python, not LLM output. An LLM may *propose* a
   trade; only `rules/` may *approve* one.
2. **Every run is reproducible.** Every simulation writes a manifest recording
   model id, quantization, temperature, seed, prompt-template hash, and data
   snapshot date. No manifest, no result.
3. **No metric without its null.** Every reported number states what a
   do-nothing or random system would score on the same data, next to it. Three
   metrics in this project were artifacts caught only by asking: a 200% recall
   (numerator counted proposals, denominator counted trades), a 5-of-5 legality
   rate (counted UNDETERMINED as legal), and a 5-of-5 counterparty match
   (proposals covered half the pair space, so chance scores 3.92 of 5). A
   number with no null is not a result and is labelled uninterpretable until it
   has one.

4. **No accuracy without its predicted distribution.** A degenerate
   predictor and a mediocre one score identically against a balanced null
   and require opposite responses: one needs a structural change, the other
   a better model. Accuracy alone cannot tell them apart. Report what the
   predictor actually emitted next to how often it was right —
   `eval/classifier_score.py` returns both or neither, deliberately in one
   function so a caller cannot print half of it.

   This is the third instance of one failure shape — a mechanism whose
   success and failure look alike at the call site. The others: a null with
   degenerate variance, where a ratio was dividing by something with no
   spread (entry #29), and a weighting that silently collapsed to uniform,
   so the weighted and unweighted results were the same number. In each the
   headline was computable, plausible and uninformative, and only a second
   statistic nobody had asked for separated working from broken.

5. **The eval harness is the product.** Agent chatter is easy and ungradeable.
   `eval/` is what makes this defensible — build it early, not last.
6. **Model-agnostic by construction.** No provider-specific code outside
   `llm/providers/`. The rest of the codebase sees one interface.

## Architecture

```
mironba/
  data/        loaders, CSV snapshots (rosters, contracts, stats)
  rules/       cap.py, trade_validator.py        ← deterministic, unit-tested
  models/      value.py, win_delta.py            ← PyMC, hierarchical
  world/       state.py, events.py, graph.py     ← SQLite + NetworkX
  llm/         client.py, schemas.py, providers/ ← see below
  agents/      base.py, gm.py, media.py, market.py, report.py
  sim/         scheduler.py, loop.py             ← event-driven, not polling
  eval/        backtest.py, scoring.py           ← the differentiator
  api/         FastAPI app
  configs/     models.yaml, scenario/*.yaml
```

## LLM layer contract

Single interface. Everything speaks OpenAI-compatible
`/v1/chat/completions` — Ollama, vLLM, SGLang, llama.cpp, LM Studio,
OpenAI, DeepSeek, OpenRouter all do natively; Anthropic and anything
exotic get a thin adapter in `llm/providers/`.

```python
class LLMClient(Protocol):
    def complete(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,  # forces structured output
        profile: str = "default",               # role -> model, see models.yaml
    ) -> BaseModel | str: ...
```

`configs/models.yaml` maps *roles* to *model profiles*, so a user with an API
key and a user on a laptop run the same code:

```yaml
profiles:
  local_fast:
    base_url: http://localhost:11434/v1
    model: qwen3.6:35b            # 3B active params — right pick for many agents
    thinking: false
    temperature: 0.8
  local_deep:
    base_url: http://localhost:11434/v1
    model: qwen3.6:27b            # dense, more stable reasoning
    thinking: true
    temperature: 0.3

roles:
  gm_agent:     local_fast
  media_agent:  local_fast
  market_agent: local_fast
  report_agent: local_deep
```

### Structured output is the failure point

Small local models drift from JSON schemas. Defenses, in order:

1. **Constrain decoding at the server.** vLLM/SGLang guided decoding
   (xgrammar/outlines); Ollama's `format` accepts a JSON schema. Pass the
   pydantic schema through — do not rely on prompt instructions alone.
2. **Validate in `llm/client.py`.** Pydantic parse, then one repair retry with
   the validation error fed back. Then fail loudly.
3. **Keep schemas small — measured, not cautious.** Two-step any complex
   action: first pick an action type from an enum, then fill that action's
   parameters in a second call. Never ask for a nested trade proposal in one
   shot.

   This was a precaution against schema drift until entry #74 measured it.
   The same trade-vs-signing question, same model, same machine, on a
   balanced 12-sentence set whose majority-class null is 6/12:

   | asked as | correct | said "signing" | median latency |
   |---|---|---|---|
   | one field among eight in `Proposal` | **6/12** | **0 of 12** | 176s |
   | its own one-field call | **12/12** | 6 of 6 | **49s** |

   The large-schema field did not score badly. It scored *exactly the null*
   and never emitted the minority class once — a constant wearing a
   classifier's type signature. Prompting does not move a constant; only
   the split did. And the split was **3.6x faster**, because the cost is in
   what the model must emit. Small schemas buy accuracy and latency from
   one cause.

   **Do not split every multi-field call on principle.** That is the same
   error facing the other way — a change made on a rule of thumb rather
   than evidence. The counter-example is in the same schema: `kind`, the
   other `Literal` classifier sitting beside `event`, scores **12/12
   inside** it, both classes emitted, indistinguishable from its dedicated
   call (#77). One field was a constant and its neighbour was flawless.
   Schema size says where to look, never what you will find.

   `mironba/llm/schema_audit.py` enumerates every call site with a derived
   field count and a declared disposition (measured, candidate, by-design).
   Measure a candidate against its own null before moving it — and note
   that "measured" does not mean "split".

### Throughput

Agent ticks are embarrassingly parallel. Ollama serializes badly under
concurrency; vLLM or SGLang batch properly. If a tick fans out to 20+ agents,
move the server to vLLM (WSL2 on Windows). Until then, cap concurrency and
keep the scheduler event-driven — an agent only wakes when an event touches
its neighborhood.

Qwen3.6 has a 256K context window, so v0 does not need retrieval. Pass the
relevant subgraph and recent event log directly. Add RAG only when a real
context measurement says to.

## Milestones

**M0 — no LLM at all.**
Load rosters, contracts, stats into SQLite. Write `rules/trade_validator.py`
encoding 2023-CBA salary matching, apron restrictions, aggregation windows.
Test against ~30 real trades from the last two seasons. Pass/fail is binary.
*Do not write an agent until this is green.*

**M1 — one agent, one tick.**
A single GM agent proposes a trade; the validator rejects or accepts; the
event log records it. Proves the LLM→rules boundary works with a local model.

**M2 — value model.**
`models/win_delta.py`: hierarchical Bayesian projection of team strength from
roster composition. A trade yields a posterior over win delta with intervals.

**M3 — full sim loop.**
Event-driven scheduler, ~8-12 ticks, GM/media/market agents, ReportAgent
summary. Timeline output.

**M4 — backtest.**
Snapshot world state before a real trade, run the sim, score simulated
follow-on moves and odds shifts against what actually happened. Report hit
rate with the model manifest attached.

**M5 — model comparison.**
Same scenario, same seed, across Qwen3.6-27B / 35B-A3B / a frontier API model.
Compare backtest scores. This turns "pluggable models" from plumbing into a
result worth putting on a resume.

## Anti-goals for v0

- Neo4j (SQLite + NetworkX is enough for 30 teams)
- Dual-platform simulation (one event log, `visibility` field)
- Fan-cluster agents (highest token cost, lowest signal)
- Polling every agent every simulated hour
- Prose personas (use structured persona params that also feed `rules/`)
