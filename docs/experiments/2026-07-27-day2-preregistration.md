# Day 2 preregistration

Day 2 uses BR-06, BR-07, and BR-08 from the frozen `bericher-v0.5`
suite. These are different historical fixes rather than repeats of one task.
All three cross files or components and require state, ordering, or lifecycle
reasoning. None is an invalid-data repair.

The Ark OpenAI-compatible endpoint supplies three separately reported models:
`glm-5.2`, `deepseek-v4-pro`, and `kimi-k2.7-code`. For every
task/model combination, direct-build and plan→build run twice from the exact
same baseline. The two workflows are adjacent and their order alternates.
All 36 attempts run serially.

Every attempt has one shared budget: 1,800 seconds, 220 tool calls, and
2,000,000 provider tokens. A plan phase may consume at most 900 seconds, 80
tool calls, and 1,000,000 tokens; its actual usage is subtracted before build.
Build starts only after a frozen plan scores 100/100 on the independent semantic
gate. A gate failure remains a real failed sample.

Provider turns have a 300-second watchdog. AI SDK retries and OpenCode session
retries are disabled for observed attempts. Failed attempts are not
automatically rerun. Request lifecycle events are used to distinguish local
budget termination from request dispatch, missing response headers or first
token, stream interruption, and processor finalization failure.

The final comparison is descriptive. Ark and earlier OpenCode Go results are
not pooled, and no statistical-significance claim will be made.
