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

## Dealership client

- A booking 500 is reported as `SlotUnbookable`. The platform source rolls back on that error, but a
  500 alone cannot prove nothing was saved, so a real booking could exist in a rare fault (D-020).
- After a lost `PATCH` or `DELETE`, a re-read could run before the request lands, and report "not
  applied" for a change that then happens. Chat should re-read before acting on that result.
- The 12 second limit is checked between attempts, so one slow attempt can run past it by up to its
  own 5 second timeout.
- `Retry-After` is read as a number of seconds only. The platform never sends it today.
- The client does not stop an amendment changing site or service. It reports the change, and the
  policy engine must enforce D-009.
- D-007's unbookable-slot table, D-008's attempt limits, and D-010's hours filter are not built
  yet. The client supplies the data they need.

## Product not built yet

Against the brief, these do not exist yet:

- Conversation memory. Each turn is read alone, so "the second one" only works with a page vehicle.
- Comparing vehicles, and offers shown as links, because the website has no offer pages.
- Structured operational logging and metrics, beyond the event log and access logs.

## Running on other computers

`.github/workflows/ci.yml` runs on every push, on real Windows, Linux, and macOS machines with Intel
and ARM chips, across Python 3.11 to 3.13 and Node 20 to 24. It covers every automated check,
`run.py`, and Docker on Linux. Gaps:

- Docker Desktop on Windows and macOS is not covered, because those build machines cannot run it.
- Each machine's browser tests use Chromium only.
- The Linux Ollama setup in `webchat/README.md` has not been tried.
- AI reply speed depends on the computer. Only one machine has been measured.
- Machines with no Docker, and no Python 3.11 or Node, cannot run it at all.
- Old or 32-bit systems, and phones as a server, are not supported.
- Ports 4010, 4173, and 4020 are fixed, as the brief and the platform set them.
- The root `README.md` does not mention port 4020 or `run.py`, and was left unchanged on request.

## Widget

- Tested in Chromium only. Safari and Firefox have not been tried, and neither has a real screen
  reader. Accessibility checks are automated, and automation finds only some problems.
- The open panel covers part of the page behind it, including inside the vehicle dialog.
- Form field errors are general, and do not yet point at the field that failed.
- Customers cannot abandon a workflow from the widget, and there is no button to delete a chat (D-017).
- A form that needs a car lists up to 50 cars as a plain dropdown.
- Vehicle comparison has no dedicated view.
- Stored stream events are not yet removed by the 30-day retention rule, because that job is not built.

## Scaling

- The concurrency gate, read cache, and per-session lock are per process (D-024).
- Vehicle cards can be up to 30 seconds out of date. Writes always recheck live data.
- `docs/SCALING.md` names metrics to add. None exist yet.

## LLM and grounding

- The validator checks values, not relationships. A real price attached to the wrong car passes.
- Model years, mileage, colours, and bare web domains are not checked.
- A customer's own stated budget passes only if the model also put it in the search filter.
- Rejected text is not stored, only its kinds, so labelling false positives needs a deliberate run.
- Accuracy figures come from 30 messages that were also used for tuning, so they are optimistic.

## Confirmation-gated writes

Pinned by strict expected-failure tests in `tests/writes/test_failure_injection.py`:

- A 500 returned after the platform really saved a booking is reported as `SlotUnbookable`, so the
  customer is told to choose another time while a booking exists.
- A customer who moves on from an unknown write can make a second booking for another time. The
  first may also exist, and parked workflows are never reconciled.
- A slow amend or cancel can land after its read-back, so the customer is told it did not apply when
  it did.

Also known:

- A change of service in an amendment is refused for now (D-022, departing from D-009).
- A keyed write older than 15 minutes is left unknown after a restart. "Check again" resends it, and
  that resend can create the booking if the original never landed, because the platform has no
  read-only way to look up a request by key.
- Only one server process may run. Recovery assumes nothing else is in flight.
- Crash tests cover process death, not power loss or disk failure.
- The confirmation token is stored unhashed in the session row, so reading the database and holding
  the session cookie would be enough to use it.
- Customers cannot resume a parked workflow, and prompts still use internal slot names.

## Workflows

- The pipeline around the machine is only partly built. The chat turn, LLM interpretation,
  composition, grounding, and the widget do not exist yet.
- A value counts as `user` if it appears in the customer's message, even if they meant something
  else by it. Internal IDs do not match customer wording, so they stay `inferred` and get confirmed.
- A reference locked in another session also locks the current session until that lock ends, so a
  different booking cannot be looked up during that time.
- The exhaustive walk uses a fixed event catalogue at one fixed time. Time-based behaviour is
  covered by named tests and the random sequences, not by the walk.

## Local LLM

- A model turn takes a median of 6.8 seconds on an M1 Pro, longer after a regeneration (D-023).
- Customers share one local model, so simultaneous chats queue (D-019).
- Replies can be plainer, and template fallback more common, than with a stronger hosted model.
- Running the full experience needs Ollama and about 6.6 GB for the model. Without them the app
  runs in degraded mode.
- Model accuracy comes from a 15-case check, not a proper evaluation.

## Not yet verified

- The widget's behaviour inside the vehicle dialog, which needs a real browser (D-015).
- Whether platform slots are true UTC or UK wall-clock times (D-010).
- Reliability of Colima on macOS 27, beyond two successful runs (D-018).
