# .context — local project context store

Everything this directory holds is **local to the repo**. Nothing is stored in an
external service. It exists so a review, its findings, and the reasoning behind
each fix survive between sessions without depending on anything online.

## Layout

| Path | What it holds |
|---|---|
| `RUNNING.md` | How to run the stack, verified commands, gotchas, and a demo that produces a real finding. |
| `review/architecture.md` | How the system fits together: services, data flow, the scan lifecycle. |
| `review/findings.md` | Every bug and defect found in the full-project review, with severity, location, and impact. |
| `review/fixes.md` | What was changed for each finding, and why. |
| `review/verification.md` | Commands run to verify the project, and their results. |
| `review/open-items.md` | What is still open, and what needs your decision. |
| `interview/` | Preparation for explaining this project: build narrative, architecture rationale with alternatives, function-by-function walkthrough, container/infra reasoning, hard questions, and debugging stories. Start at `interview/00-START-HERE.md`. |

## Conventions

- Findings are numbered `F-01`, `F-02`, … and keep their number permanently.
- Every fix in `fixes.md` cites the finding number it closes.
- Severity means: **Critical** blocks the product from working, **High** breaks a
  documented feature or opens a security hole, **Medium** degrades behavior under
  real conditions, **Low** is correctness or hygiene.
