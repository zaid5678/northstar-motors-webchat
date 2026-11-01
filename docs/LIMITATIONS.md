# Known limitations

Gaps in the Northstar Motors webchat, kept up to date as work proceeds. Nothing has been built yet,
so these are the gaps already known from `docs/PLATFORM-NOTES.md` and `docs/DECISIONS.md`.

## Platform behaviour we cannot change

- A workshop slot released by cancellation can never be rebooked. Chat hides the slots it knows
  about, but a slot cancelled elsewhere is found only when a booking fails (D-007).
- A platform reset is not detected, so the unbookable-slot table can hide good slots until their
  date passes (D-007).
- A reset also removes records that stored conversations still refer to.
- The platform has no attempt limit on lookup. Ours protects only requests that pass through chat
  (D-008).
- `GET /admin/api/summary` returns customer records with no key, with CORS open to all origins.
- The platform's request log keeps free text unredacted, so details typed into notes or messages
  are stored there (D-017).
- A new booking can be made in a slot with a past date, and past slots stay listed. Chat filters
  them, but the platform does not.

## Missing from the platform

- No privacy notice exists, only a contact address. Chat's statement is factual and has not been
  legally reviewed (D-011).
- Test drives cannot be found, amended, or cancelled. Chat can only send a request to sales, and
  bookings from other sessions cannot be seen (D-014).
- Registration and contact details on a workshop booking cannot be amended.
- Part-exchange estimates do not check that the vehicle is real.
- Vehicle state cannot be changed through the API, so stale-availability handling is tested only
  with mocks.

## Differences from the website or platform

- Chat offers fewer slots than the platform, because it hides those outside department hours
  (D-010).
- Chat does not show used-car monthly prices, though the website does (D-012).
- Only vehicles have stable website links. Offers and services do not.

## Local LLM

- A typical turn takes about 7 seconds on an M1 Pro, longer after a regeneration (D-019).
- Customers share one local model, so simultaneous chats queue (D-019).
- Replies can be plainer, and template fallback more common, than with a stronger hosted model.
- Running the full experience needs Ollama and about 6.6 GB for the model. Without them the app
  runs in degraded mode.
- Model accuracy comes from a 15-case check, not a proper evaluation.

## Not yet verified

- The webchat's own `docker compose` service, which does not exist yet (D-018).
- The widget's behaviour inside the vehicle dialog, which needs a real browser (D-015).
- Whether platform slots are true UTC or UK wall-clock times (D-010).
- Reliability of Colima on macOS 27, beyond one successful run (D-018).
