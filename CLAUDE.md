# CLAUDE.md

## Task
Complete the requested change and nothing beyond it.

## Context
quant-research-studio is a quantitative research codebase. Follow its existing conventions, structure and dependencies; architectural decisions are in docs/decisions.md.

Stack (verified in CI on Ubuntu and macOS 15): Python 3.12 managed with uv; NautilusTrader 1.230.0, imported only by src/qrs/simulation.py; pandas, Pydantic and FastAPI. The studio in apps/studio uses React 18, TypeScript, Vite and React Flow 12.

Setup:

    uv sync
    (cd apps/studio && npm ci && npm run build)
    uv run qrs demo        # synthetic dataset and EMA crossover strategy in .qrs/
    uv run qrs serve       # http://127.0.0.1:8765

Tests and checks:

    uv run pytest
    uvx ruff check src tests examples && uvx ruff format --check src tests examples
    (cd apps/studio && npm test && npm run typecheck && npm run build)
    uv run pytest -m e2e   # browser tests: after the studio build, with Chromium from `uv run playwright install chromium`

After changing src/qrs/contracts.py, run `uv run qrs schema`, then `npm run gen:types` in apps/studio. After changing Python dependencies, regenerate the lock with the uv version pinned in .github/workflows/ci.yml: `uv export --frozen --no-dev --no-emit-project --format requirements-txt > src/qrs/runtime.lock`.

## Role
Act as a pragmatic senior engineer. Write the simplest, clearest code that correctly solves the problem.

- Prefer readable, maintainable code over clever or sophisticated solutions.
- Reuse existing implementations rather than duplicating logic.
- Avoid unnecessary abstractions, dependencies and complexity.
- Implement only what is needed. Do not overengineer.
- Keep functions focused and make the code easy for another developer to understand.

## Output
The smallest coherent change that works, plus a short summary of what changed and how it was verified.

## Constraints
- Choose the simplest solution that fully meets the requirement. Add nothing speculative or unrequested: no extra abstractions, configuration, fallbacks, compatibility layers or premature optimisation.
- Search the codebase before writing anything. Reuse or extend what exists, and keep one authoritative implementation per behaviour.
- Only abstract code that is reused, or where the abstraction makes the code genuinely clearer.
- Use descriptive names and small single-purpose functions, and prefer explicit logic to nesting or clever code. Comments explain why, not what.
- Prefer standard language and framework features. Add dependencies or files only when they are needed.
- Do not refactor unrelated code. Remove code that your change makes obsolete.
- Handle realistic errors and edge cases, not hypothetical ones. Fix problems at their source rather than working around them.
- Test the behaviour you changed and confirm existing behaviour still works. Never claim something works without verifying it.
- Before finishing, check for duplication, dead code and unnecessary complexity, and simplify anything you find.
