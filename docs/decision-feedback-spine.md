# The decision → outcome → feedback spine

Status: implemented (offline, deterministic).  Modules: `tracelane/spine/`,
`tracelane/decision_orchestrator.py`.  CLI: `tracelane decide …`.

## Why this exists

TraceLane's original loop is **answer-oriented**: gather evidence, analyze,
debate, finalize an `AgentAnswer`, grade it.  That answers "did the agent
produce a grounded answer?"  It does **not** answer the harder question the
field is now asking: *can a harness close the loop on its own decisions — can
it commit to a stance, watch the world resolve it, and feed the result back so
the next run is better?*

The spine adds that second half.  It is the mechanism a research harness needs
to turn noisy agent behavior into a verifiable signal: **evidence → typed
signal → committed decision → factual outcome → deterministic feedback**, all
journaled to an append-only, hash-chained ledger.

## The chain

```
gather → analysts (TypedSignal each) → fuse (deterministic) → debate? (policy)
       → decide (DecisionRecord) ──► ledger
       → resolve (OutcomeRecord, from a point-in-time-true Resolution)
       → feedback (FeedbackRecord, per-signal attribution)
       → propose_reliability_updates (reviewable candidates, never live config)
```

Design invariants, enforced in code:

* **Signals are admissible or they abstain.**  A non-abstaining `TypedSignal`
  must cite at least one retained `evidence_id` and carry a directional view
  with `confidence > 0`.  An abstaining signal must say why and carries no
  direction.  This rejects the failure mode where fluent prose is mistaken for
  verified signal.
* **Outcomes are facts, not reflections.**  An `OutcomeRecord` is resolved
  from a point-in-time-true `Resolution` of the world, never from a second LLM
  call.  An unresolvable subject yields an `invalid` outcome with a reason, so
  unresolved decisions are visible rather than silently dropped.
* **Feedback is deterministic attribution.**  `decision_correct` judges the
  committed stance against the actual world direction under an explicit
  deadband; per-signal verdicts blame individual analysts, not the whole graph.
* **Self-improvement is a candidate, not a command.**  Reliability proposals
  shrink observed analyst accuracy toward a 50% prior and require a minimum
  sample.  They are marked `requires_walk_forward` / `insufficient_sample` and
  cannot mutate a live run until a separate frozen validation accepts them.
* **The ledger is tamper-evident.**  Every record is content-addressed
  (id derived from canonical content, never wall clock or randomness) and
  hash-chained to its predecessor; cross-record references must already be
  journaled, so "signals rest on retained evidence" is auditable end to end.

## How it maps to frontier work

| Mechanism | Frontier reference | What we took |
|---|---|---|
| TypedSignal / abstain | ReAct (T-A-O), grounding graders | reasoning must bottom out in cited evidence or abstain |
| Deterministic fusion | Anthropic *Building Effective Agents* | aggregation is code, not another model call; independence discounting stops one announcement echoed by N roles counting N times |
| Debate as a policy | Anthropic *Multi-Agent Research System* | debate is switchable and ablatable (`always`/`conditional`/`never`), triggered by disagreement/coverage, not always-on |
| Outcome as fact | *Effective Harnesses for Long-Running Agents* | resolve against the world, not against the model's own judgment |
| Reliability proposals | reward / feedback loops; RLHF-style preference data | turn resolved outcomes into shrunk, reviewable weight candidates — the smallest runnable co-evolution loop |
| Hash-chained ledger | trace-first observability | every run replayable; tampering detectable on read |

## Measured results (synthetic `fixtures/decision-v0.1`, fully offline)

These are reproducible: `tracelane decide ablate-debate …` and
`tracelane decide ablate-feedback …`.

**Debate ablation** (`debate on` vs `off`, 6 tasks, seed 7):

| arm | accuracy | mean model calls | mean tokens |
|---|---|---|---|
| debate_on | 1.000 | 4.17 | 884 |
| debate_off | 1.000 | 3.17 | 619 |

Δ accuracy `+0.000`, Δ tokens `+266`.  On a suite whose signals are already
clean, debate changes nothing and costs ~43% more tokens — which is exactly
the point of measuring it: **the ablation prices debate and shows when it is
not load-bearing.**  A noisier suite (higher fusion disagreement) is where the
`conditional` arm should pay for itself.

**Feedback-loop ablation** (`static` vs `self_improving`, per-round accuracy):

```
static         = [1.000, 1.000, 1.000, 1.000, 1.000]
self_improving = [1.000, 0.857, 1.000, 1.000, 1.000]
```

Learned analyst reliability (higher = more trusted), by round:

| analyst | round 3 | round 5 | round 8 | trajectory |
|---|---|---|---|---|
| fund (consistently right) | 0.756 | 0.818 | 0.868 | rising |
| sentiment | 0.688 | 0.750 | 0.808 | rising |
| news | 0.565 | 0.600 | 0.643 | rising |
| risk (non-directional) | 0.500 | 0.500 | 0.500 | flat (correctly untouched) |
| contrarian (consistently wrong) | 0.435 | 0.400 | 0.357 | falling |

Two honest findings, both of which argue *for* the guardrails:

1. **The loop learns the right ordering.**  The consistently-wrong contrarian
   is monotonically down-weighted; the consistently-right fundamentals analyst
   is monotonically up-weighted; the non-directional risk desk is left at the
   0.5 prior.  This is the co-evolution mechanism working end to end.
2. **Self-improvement is not monotonic.**  The self-improving arm dips to
   0.857 in round 2 before recovering — at low sample the shrinkage prior has
   not yet stabilized the weights, so a couple of outcomes can briefly
   mis-weight an analyst.  This is precisely why proposals are
   `requires_walk_forward` and never auto-applied to a live run.

## What is deliberately out of scope (and why)

* **No real market data in the public repo.**  The synthetic decision suite is
  PIT-safe and reproducible.  A private adapter can drive the identical spine
  from licensed real-world data for a real outcome→feedback curve; the data
  itself stays out of the repository (licensing / credential boundaries).
* **The stub does not reason.**  `DeterministicStubRuntime` reflects the
  analyst stance the orchestrator supplies, exercising the same code path a
  real runtime would drive.  Swapping in a real model changes only the
  runtime, not the spine.

## Showcases: distilling a real TradingAgents research run

`scripts/distill_research_showcase.py` is an offline, deterministic adapter
that turns the trace of one real TradingAgents multi-agent research run (a
``fullflow`` JSON: analyst reports, a bull/bear debate, a risk debate, and a
final rating) into a standard decision-suite task under `fixtures/showcase/`.
It preserves the *structure* of a real investigation — the analyst roster that
actually ran, a deterministic per-analyst stance derived from structural
signals, and a resolution mapped from the run's final rating — while dropping
the *content*: the real ticker and company identity are replaced by a synthetic
subject id (`SHOWCASE-###-XXXXXX`), and every report's prose is replaced by a
short synthetic note tagged only with its source type.  No model is called and
no wall clock is read, so the showcase is reproducible and the repository stays
clean of licensed data and credentials.

```bash
python scripts/distill_research_showcase.py path/to/FULLFLOW.json --out fixtures/showcase
tracelane decide ablate-debate   --suite fixtures/showcase --artifacts artifacts/showcase
tracelane decide ablate-feedback --suite fixtures/showcase --rounds 3
```

A Hold-rated run distills into a *standoff*: the roster splits bull/bear so
weighted conviction cancels (high fusion disagreement, near-zero score), which
is exactly the regime where a debate policy should earn its cost.  Distilling
several runs with different final ratings (Buy / Hold / Sell) into one suite is
what turns a single showcase into an ablation with real discriminating power.

## Controlled ablation suites

A handful of distilled real runs is a poor substrate for ablation: ground
truth is unresolved, the sample is tiny, and the noise is uncontrolled.
`scripts/generate_ablation_suite.py` generates a suite whose *signal-to-noise
is known by construction*, so a debate or feedback ablation must surface its
effect and that effect is attributable to a named variable:

* a block of **reliable** analysts (tunable per-analyst reliability);
* a **noisy** analyst who is *confident but systematically wrong* — the key
  ingredient for the feedback loop, since the static arm keeps getting misled
  by that confidence while the self-improving arm learns to down-weight it;
* a tunable **disagreement fraction** of contested tasks for the debate
  ablation.

```bash
python scripts/generate_ablation_suite.py --out /tmp/suite \
    --tasks 12 --seed 7 --disagreement-fraction 0.3 --reliability 0.8
tracelane decide ablate-feedback --suite /tmp/suite --rounds 5 --min-samples 3
```

Measured across seeds 1, 7, 42, 123, 999 (12 tasks, 5 rounds): the
self-improving arm beats the static arm by **+0.417 to +0.667** final-round
accuracy every time, and the systematically-wrong analyst's learned reliability
is driven to **0.125** while the reliable analysts stay high.  The static arm
never improves (it cannot learn); the self-improving arm converges toward the
reliable ceiling.  That is the co-evolution mechanism working, reproducibly,
with the effect attributable to the feedback loop and nothing else.

Distilled real runs and generated suites complement each other: the real runs
are *authenticity anchors* (this structure really occurs), while the generated
suites are *causal substrates* (this mechanism really does the work).

## Product path

1. Ship the spine as the auditable decision layer behind any research agent.
2. Drive it from a real runtime + a real (private) data adapter to produce a
   real outcome→feedback curve and failure-mode taxonomy.
3. Use `propose_reliability_updates` output as reviewable candidates in a
   frozen walk-forward gate — the harness improves its own aggregation as
   outcomes accumulate, with a human/sign-off boundary before any candidate
   becomes policy.
