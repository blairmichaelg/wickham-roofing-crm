---
trigger: always_on
---

# Efficient Workflow & Verification Rules

These rules apply to every task in this workspace. Follow them without
exception unless the user's current prompt explicitly overrides one.

## 1. Conserve AI credits and tool calls

- Never run the full test suite (`pytest tests/`) more than once per task.
  While developing, run ONLY the specific test file(s) relevant to the
  change you just made. Run the full suite exactly once, at the very end,
  immediately before committing/pushing.
- Never run `ruff` or `mypy` scoped to only the files you touched as a
  substitute for the real check. Run the full-scope command this repo's
  CI actually uses (check `.github/workflows/` for the exact command) and
  run it exactly once, at the end, not repeatedly during development.
- Do not re-read the same file multiple times in a session unless it has
  changed since your last read. Cache what you've already learned about
  the codebase in your own working context instead of re-fetching it.
- Do not open a browser, launch a live URL, or use browser-based tools
  unless there is no other way to verify the thing you need to verify.
  Prefer: reading source files, running local test commands, querying the
  local database directly, and reading logs. Only open a browser as a last
  resort when the user explicitly asks you to check live UI behavior.
- Do not perform web searches for information that is already answered by
  this repo's own documentation, code comments, or your own prior
  knowledge. Only search the web for facts that are genuinely external
  (e.g., a third-party API's current schema) and not derivable from the
  codebase itself.
- If you cannot recall the exact wording of an earlier instruction in this
  session, do not spend time trying to recover it from internal session
  files, caches, or logs (e.g., parsing `.pb` files, decompression
  attempts, scanning app-data directories). That is out of scope and wastes
  time and credits. Instead, briefly state your best understanding of the
  task and proceed, or ask the user a short clarifying question.

## 2. Git discipline

- Never create a branch. Never open a pull request. Work directly on
  `main` and push directly to `main` only once all work for the current
  task is complete, tested, and documented.
- Make focused, logically grouped commits as you go, but only push once at
  the end of the task — not after every small edit.
- Never commit or push scratch scripts, debug files, temporary database
  copies, or anything under a `scratch/` directory. Keep those local and
  untracked.
- If a task requires a one-time data migration or backfill on the
  database, implement it as a proper numbered migration file in
  `app/core/migrations/` (following this repo's existing naming and `up()`
  convention), not as an ad-hoc script run once on a local machine. Ad-hoc
  local-only data mutations do not travel with the codebase and must be
  avoided for anything that needs to be reproducible across environments.

## 3. Decisiveness and scope discipline

- When a user's prompt gives explicit, final decisions (exact values,
  exact library choices, exact parameter names), implement exactly what
  was specified. Do not substitute your own design choice, even if you
  think it's better, without flagging the deviation clearly in your final
  summary.
- Do not introduce any new third-party service, API, or dependency that
  was not explicitly requested — including "free" ones. If you believe an
  external dependency is genuinely necessary to solve a problem, stop and
  explain why in your summary rather than silently adding it.
- Do not add feature flags, alternate code paths, or "TODO: decide later"
  comments. Build exactly what was asked, as a single final implementation.
- Do not expand scope beyond what was asked. If you notice an unrelated
  bug or improvement opportunity while working, note it in your final
  summary instead of fixing it unprompted.

## 4. Verification honesty

- Never report a task, test, or documentation item as "complete" unless
  you have actually re-read the file after editing it to confirm the
  change is present and correct.
- If a full test suite run reports N passing tests, that number must
  reflect an actual full run you just performed — never state a test count
  you have not personally just observed.
- If you skip or deliberately defer part of a requested task (due to
  ambiguity, risk, or scope), say so explicitly in your final summary.
  Do not let a walkthrough imply full completion when something was
  skipped.

## 5. Documentation discipline

- Any task that changes user-facing behavior must update, in the same
  pass: `CHANGELOG.md`, and the relevant file(s) under `docs/` if the
  change affects how admin, accounting, operations, or field-rep users
  interact with the system.
- Keep `pyproject.toml` version and `README.md` version/test-count badges
  in sync with `CHANGELOG.md` whenever either changes.
