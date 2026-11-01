Project: Northstar Motors AI webchat (see PRODUCT-BRIEF.md). Stack: FastAPI, Pydantic v2,
httpx, SQLAlchemy + SQLite + Alembic, SSE, React widget in Shadow DOM, pytest + respx.

Working rules:
- The LLM is one stage in a pipeline my code owns. It never executes writes, never
  supplies prices/availability/hours, and never decides what is allowed.
- Before building anything non-trivial, give me 2-3 options with tradeoffs and a
  recommendation. Wait for my decision. Don't pick for me.
- Record every decision in docs/DECISIONS.md (context, options, choice, why,
  consequences, what would make us revisit it).
- Tests first for anything with side effects. No feature is done without a failing
  test that now passes.
- If a request is ambiguous, ask before proceeding.
- Never touch the dealership platform's behaviour or seed data. API key stays server-side.
- Keep a docs/LIMITATIONS.md of known gaps as we go.
- End each phase with a short summary: what changed, what's untested, what's risky.