# Day 3 BR-10 adjudication amendment

The Day 3 v2 queue was stopped after eight completed BR-10 slots when two independent
DeepSeek direct builds received frozen grader scores of 0/100 despite implementing the
required behavior with different, equivalent structures.

The frozen v1 gate and grader are not changed. Their outputs remain part of the record.
Versioned v2 adjudicators correct two implementation-specific assumptions:

- the plan gate now accepts `A/C/M` as the conventional shorthand for added, copied and
  modified staged paths;
- the functional grader no longer requires the variable name `BIG`, one exact batch
  control-flow shape, or `%%~z` when PowerShell provides equivalent Windows size detection.

Counterexamples still reject push trapped inside the new-commit branch, omission of
added/copied paths, and plans that explicitly skip push when there are no new changes.
Observed replay preserves partial scores: corrected adjudication does not automatically
turn every BR-10 workspace into a pass.

The Kimi R1 direct slot was operator-interrupted during diagnosis before a provider request
produced any model or tool usage. Its raw trace remains excluded as an operator
interruption, and a new suffixed attempt ID replaces it.

The original frozen v1 gate continues to make every remaining primary-matrix build
decision. Plans blocked only by a demonstrated v1 false negative may later receive a
separate corrected-gate replay using the original plan and remaining shared budget. Replay
results must not be merged into the primary matrix.
