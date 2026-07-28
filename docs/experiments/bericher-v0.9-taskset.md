# BeRicher v0.9 frozen coding tasks

v0.9 adds three historical tasks outside invalid-data handling. They are intended for a
future cross-task matrix, not retroactively added to Day 2.

| Task | Baseline | Editable scope | Main reasoning |
|---|---|---|---|
| BR-10 | `ab3ccc82` | Windows and POSIX sync scripts | Commit/push state, staged-file safety, cross-platform parity |
| BR-11 | `e8ca57ae` | Scheduler and real-trading helpers | State ordering, next-day timing, per-simulation failure isolation |
| BR-12 | `4d236860` | K-fold loop and two launchers | Epoch-state timing, control-arm parity, failure short-circuit |

BR-11 and BR-12 are explicitly cross-component. BR-10 provides a cross-platform state
machine where “nothing new to commit” must not mean “nothing to push.”

## Grading

Each hidden grader exposes weighted functional slices:

- BR-10: ahead-commit push 35, shell preflight 25, batch preflight 25, abort/date semantics 15;
- BR-11: discovery 10, trading-day resolution 10, fill synchronization 20, NAV-based orders
  20, pipeline ordering and guards 40;
- BR-12: per-epoch warmup state 50, cross-platform recipe parity 20, two-arm contract 20,
  failure/main-only control flow 10.

The highest weights sit on the state or timing semantics that motivated the historical
change. Parameter wiring and surface syntax cannot earn most of the score by themselves.
The historical baselines score 0 and the target commits score 100 under these graders.

All three tasks freeze a 1,800-second, 220-tool-call, 2,000,000-model-token budget and
retain protected data, model, paper-trading and output paths.
