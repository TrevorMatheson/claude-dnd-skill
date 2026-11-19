# Build pipeline — MyDM

How a feature goes from idea to merge. Each role works in its own context
and hands the next one a file, so nothing depends on one long chat.

| # | Role | Who | Reads | Produces | Gate |
|---|------|-----|-------|----------|------|
| 1 | Spec | `/grill-me` with the user | the idea, `GRILL.md`, the code | `docs/specs/<date>-<slug>.md` | user sets `status: approved` |
| 2 | Plan | `architect` agent | the spec, the code | `<spec>.plan.md` (saved by the main session) | user approves the plan |
| 3 | Per step: tests first | `qa` agent, mode tests-first | spec, plan step | failing tests in `tests/` | tests fail for the right reason |
| 4 | Per step: build | main session | spec, plan step, failing tests | code, one commit per step | QA's tests pass, full suite green |
| 5 | Verify | `qa` agent, mode verify | spec, the running app | AC pass/fail table with evidence | every in-scope AC passes or is marked "needs the user" |
| 6 | Review | `reviewer` agent | spec, plan, `git diff main...HEAD` | ranked findings | "ready to merge" |
| 7 | Merge | the user | everything above | merged PR, spec set to `status: built` | |

Steps 3–5 repeat for each plan step. Run step 6 once per branch.

## Skips

- **A bug fix with a known cause:** start at step 3 (a failing test that
  reproduces the bug), then 4, 5 (the affected area only) and 6.
- **A typo, docs, or a test-only change:** step 6 only.

## When something fails

- **QA finds a failure:** it goes back to step 4 with QA's repro. QA never
  fixes product code.
- **The Architect or a builder finds the spec can't be built as written:**
  stop and take it back to the user. A spec decision changes only with the
  user's yes, recorded in the spec.
- **The Reviewer says not ready:** fix the blocking findings, then re-run
  QA verify on whatever the fixes touched, then review again.
