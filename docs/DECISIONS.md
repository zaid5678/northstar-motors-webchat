# Decisions

The record of design decisions for the Northstar Motors webchat. Each entry gives the context, the
options, the choice, why, the consequences, and what would make us revisit it.

D-007 to D-018 answer questions Q1 to Q12 in `docs/PLATFORM-NOTES.md` section 5. The user delegated
them on 2026-11-01 ("choose whichever you think is best to increase complexity"), then added
"make sure the option is better in the customer sense". Customer benefit is therefore the deciding
test: the fuller option was taken only where it is better for customers. D-012 does not take the
more complex option, and it says why.

Four facts could not be delegated. The user answered them on 2026-11-01:

- Docker: install it if that is the best option (D-018).
- Paid LLM key: none, so use a free option (D-019, replacing D-016).
- Approved privacy text: none exists (D-011).
- Platform bug report: goes to the user (D-007).

---

## D-001: Turn pipeline

**Date:** 2026-11-01. **Status:** accepted.

**Context:** the LLM must never execute writes, supply prices, availability, or hours, or decide
what is allowed (`CLAUDE.md`). The platform will not protect us either: it has no identity check
on amend or cancel, and it returns misleading successes (`PLATFORM-NOTES.md` sections 2 and 3).

**Options:** this pipeline was proposed as a single design, with the choices below inside it. No
alternative pipeline was compared.

**Choice:** every turn runs through eight stages.

| Stage | Responsibility | LLM? |
| ----- | -------------- | ---- |
| 1. Ingest | Authenticate the session, drop repeat submissions, lock the session, record the message. Treat page context as untrusted | No |
| 2. Pre-router | Handle confirm and cancel clicks, verification forms, workflow form steps, and lockouts | No |
| 3. LLM interpretation | Return a schema-checked intent, entities, and proposed action, referring to items only by handles we issued | Yes |
| 4. Policy engine | Pure function of state, proposal, and facts: allow, clarify, verify, confirm, or deny | No |
| 5. Execute | Run reads; for writes, recheck live state, record the intent, send, record the outcome | No |
| 6. Compose | Code builds cards, confirmations, and notices; the LLM writes the conversation around them | Yes |
| 7. Ground | Check the text against this turn's facts; regenerate once, then use a template | No |
| 8. Persist/stream | Save first, then send over SSE with event IDs | No |

**Why:** the LLM appears only in stages 3 and 6, and both outputs are checked by code before they
have any effect. Authority sits in stages 2, 4, and 5, which can be tested without a model.

**Consequences:**

- Progress events stream during the turn, but reply text is held until it passes grounding, so
  replies do not stream word by word.
- The LLM is called up to twice per turn, plus any regeneration, which adds latency and cost.

**Revisit if:** the held-back reply feels too slow in use. Sentence-by-sentence release after
grounding is the next step.

---

## D-002: Workflow state

**Date:** 2026-11-01. **Status:** accepted.

**Context:** the workflows are short and mostly linear: gather details, verify, confirm, execute.
State must be restored on every request and survive a page refresh.

**Options:**

- **A. Hand-rolled transition tables.** No dependency, easy to store and test exhaustively, but we
  own the guards and diagrams, and nesting is awkward.
- **B. `transitions` library.** Mature, with guards, hierarchy, and diagrams, but it attaches
  generated methods to models, weakens typing, invites side effects in callbacks, and must be
  rebuilt per request.
- **C. XState-style statecharts.** Strongest for nested and parallel states, but XState is
  JavaScript, so this means a Node sidecar outside the stack or writing our own interpreter.

**Choice:** A.

**Why:** a pure `(state, event) → (state, effects)` function fits the problem and keeps side
effects out of transitions, so the policy engine and Execute stay separate. B and C solve
complexity these workflows do not have.

**Consequences:**

- Tests can loop over every state and event pair.
- We write our own guards and any diagrams.
- Side questions in the middle of a workflow must be handled explicitly.

**Revisit if:** workflows start to nest, such as pausing a booking for a side question and
resuming across several levels of state.

---

## D-003: Session storage

**Date:** 2026-11-01. **Status:** accepted.

**Context:** a connection failure on a write leaves its outcome unknown, so the attempt must be on
record before it is sent. Conversations must survive a refresh, and SSE must be able to resume.

**Options:**

- **A. Event log only.** Full audit and replay, but every read needs a projection, old event
  formats must keep working, and it is the most code.
- **B. Mutable row.** Simplest, but has no history, and a crash between a platform call and the
  save loses what happened.
- **C. Hybrid.** An append-only event table plus a session row holding current state.

**Choice:** C.

**Why:** the event table gives a durable record of write attempts and outcomes, and supports SSE
resume. The row keeps reads simple without a projection layer.

**Consequences:**

- Event and row updates must share one SQLite transaction, or the two can drift.
- Events hold personal data, so they fall under the retention rule (`PLATFORM-NOTES.md` Q11).
- Deleting a session means deleting its events and row together.

**Revisit if:** the row becomes hard to rebuild from events, or cross-session analytics are needed.

---

## D-004: Write safety

**Date:** 2026-11-01. **Status:** accepted.

**Context:** `PATCH` and `DELETE` accept no idempotency key. The platform's idempotency hash uses
the raw body, so a reformatted retry gets `IDEMPOTENCY_CONFLICT`. The LLM must not decide when a
write happens.

**Options:**

- **A. Confirmation tokens plus idempotency keys.** Single-use, expiring, and tied to the session
  and exact action, at the cost of more state and expired-card handling.
- **B. One pending action plus idempotency keys.** Less to build, but a stale card or second tab
  can approve the wrong action unless an action ID is added.
- **C. Idempotency keys only.** Least code, but nothing proves the customer agreed, which breaks
  the `CLAUDE.md` rule.

**Choice:** A, with the token implemented as a random ID for a server-side pending-action row, not
a signed token.

**Why:** B needs an action ID to be safe, which makes it A. Tokens are also the only protection
against repeats on `PATCH` and `DELETE`.

**Consequences:**

- The pending-action row stores the exact request body and idempotency key, and retries reuse both.
- Every create sends a key, including the writes where the platform only recommends one.
- Tokens are consumed atomically before the request is sent, and are never shown to the LLM.
- If an amend or cancel has an unknown outcome, we re-read the booking rather than resend.
- A typed "yes" is accepted only when exactly one action is pending.
- Expired or superseded cards need a clear customer-facing state.

**Revisit if:** expiry causes real friction for customers.

---

## D-005: Grounding

**Date:** 2026-11-01. **Status:** accepted.

**Context:** the LLM must not supply prices, availability, or hours, and the brief asks for a
natural conversation. Several traps are claims rather than figures, such as describing a sold car
as available or a `WAIT-…` reference as a queue place.

**Options:**

- **A. Post-generation validator only.** Natural prose, but claims are hard to extract reliably,
  and rejections add delay.
- **B. Structured output only.** Figures cannot be invented, but replies can feel stiff, and any
  free-text field stays unchecked.
- **C. Both.** Prose with fact placeholders, then a validator over the result, at the highest build
  cost and with tuning needed for false rejections.

**Choice:** C.

**Why:** placeholders stop invented figures, and the validator catches what they cannot: bare
values and unsupported claims.

**Consequences:**

- The LLM writes placeholders such as `{price:vehicle_2}`, and code fills them in.
- The validator rejects any bare price, date, time, phone number, reference, or APR, plus
  unsupported availability or promise wording.
- A failure regenerates once, then falls back to a template.
- Rejections and fallbacks are logged, because a high rate means the prompts or rules need work.

**Revisit if:** false rejections push too many replies onto templates.

---

## D-006: LLM failure mode

**Date:** 2026-11-01. **Status:** accepted.

**Context:** the brief requires clear unavailable and error states, and recovery from failed or
interrupted requests. Stages 1, 2, 4, 5, and 7 of the pipeline do not depend on the LLM.

**Options:**

- **A. Deterministic fallback.** Customers can do most things, but it means building and
  maintaining a second, weaker interpreter.
- **B. "Service unavailable" message.** Simple and honest, but blocks simple tasks and can strand
  a customer in the middle of a booking.
- **C. Degraded mode.** Work that needs no interpretation continues, and free text gets an honest
  message plus buttons.

**Choice:** C.

**Why:** most of the pipeline still works without the LLM, so stopping everything would throw that
away, and a rule-based interpreter would cost more than an outage justifies.

**Consequences:**

- Confirm clicks, form steps, and template replies keep working, so a confirmed booking still
  completes during an outage.
- Free text gets "I'm having trouble understanding", plus buttons for hours, contact details, and
  a callback.
- Timeouts and malformed output count as failures, with one retry.
- A platform outage is separate and shows the unavailable state.
- There are two chat experiences to design and test.

**Revisit if:** outages are common enough that free-text search needs a fallback.

---

## D-007: Slots that cannot be rebooked (Q1)

**Date:** 2026-11-01. **Status:** accepted (delegated).

**Context:** a slot released by cancellation is listed as available, but booking it always returns
500 with `retryable: true` (`PLATFORM-NOTES.md` 3.7). The platform cannot be changed.

**Options:**

- **A.** Treat a 500 on a booking write as "that slot can't be booked" and offer others.
- **B.** Also hide slots cancelled through chat.
- **C.** Also warn the customer before cancelling.

**Choice:** all three, backed by a shared table of unbookable slots.

**Why:** the fault belongs to the slot, so it affects every customer, not one session. A shared
table protects the next customer as well.

**Consequences:**

- Slots cancelled through chat are hidden until their date passes.
- A slot that returns 500 is hidden for one hour, because the 500 might be a passing fault.
- A booking 500 is never retried, whatever `retryable` says.
- The cancel confirmation warns that the time may not be available again.
- A platform reset leaves stale entries that hide good slots (`LIMITATIONS.md`).
- The bug was reported to the user on 2026-11-01, and the workaround stands until the platform
  changes. It reproduced on the real `python:3.12-alpine` image.

**Revisit if:** the platform is fixed, or a way to detect resets appears.

---

## D-008: Verification policy (Q2)

**Date:** 2026-11-01. **Status:** accepted (delegated).

**Context:** the platform checks identity only on lookup, has no attempt limit, and will amend or
cancel any booking given its ID.

**Options:**

- **A.** Four-factor lookup with session-only limits.
- **B.** A, plus a limit per booking reference across all sessions.
- **C.** Verify once per conversation, for any booking.

**Choice:** B.

**Why:** an attacker can start a new session to reset a session-only limit, so the limit must also
follow the reference.

**Consequences:**

- Retrieve, amend, and cancel all need the four-factor lookup.
- The verified booking ID stays server-side, tied to one session and one booking.
- Verification ends after 15 minutes without activity, so a customer who is mid-task is not asked
  to verify again. It survives a page refresh inside that window.
- A new booking needs a new check.
- 5 failures lock the session or the reference for 15 minutes.
- A reference lock could briefly block the real customer, who is pointed to dealership contact
  details instead.

**Revisit if:** genuine customers hit lockouts in testing.

---

## D-009: Amendment limits (Q3)

**Date:** 2026-11-01. **Status:** accepted (delegated).

**Context:** `PATCH` to a slot elsewhere silently changes the dealership and service.

**Options:**

- **A.** Same service and site only.
- **B.** Same service, any site, with explicit confirmation of the change.
- **C.** Anything the platform allows.
- **D.** B, plus a guided change of service as a two-step replacement.

**Choice:** D.

**Why:** customers do change their minds about a service, and this keeps every change visible. The
new booking is made first, so a failure never leaves them with no appointment.

**Consequences:**

- A changed site or service appears on the confirmation card as old and new values.
- A service change uses one confirmation for two writes: book the new slot, then cancel the old.
- If the cancel fails, the customer has two bookings. Chat must say so plainly and retry by
  re-reading the booking, as in D-004.
- Each step is recorded as its own event. The old slot joins the D-007 table.

**Revisit if:** the two-booking failure proves confusing, in which case service changes become a
manual cancel and rebook.

---

## D-010: Slot times (Q4)

**Date:** 2026-11-01. **Status:** accepted (delegated).

**Context:** docs say timestamps are UTC, and no document states a dealership timezone. 22 of 44
Saturday workshop slots start after service closes.

**Options:**

- **A.** Show slots as stored.
- **B.** Convert to Europe/London and show all.
- **C.** Convert, and hide slots starting outside the department's hours for that date.

**Choice:** C, using the same date-aware hours logic for workshop and test-drive slots.

**Why:** the docs are the only stated source for UTC, and sending a customer to a closed department
is worse than offering fewer slots. Holiday exceptions already require date-aware hours.

**Consequences:**

- Chat shows fewer slots than the platform and the Systems Console, and each hidden slot is logged.
- Slots that overrun closing stay visible. Chat states the start time and duration, and promises no
  finish time.
- Tests must pin a summer date, because the UK stays on GMT until late March 2027.

**Revisit if:** slots turn out to be UK wall-clock times stored with a `Z`.

---

## D-011: Part-exchange contact details and privacy (Q5)

**Date:** 2026-11-01. **Status:** accepted (delegated). No approved text exists, so the factual
statement ships.

**Context:** the platform needs name, email, and phone before it gives an estimate. It supplies a
privacy contact address but no privacy notice.

**Options:**

- **A.** Collect contact details without comment.
- **B.** Explain why, show the privacy contact, and confirm first, using a factual statement.
- **C.** B, with notice text held as replaceable configuration.

**Choice:** C.

**Why:** the customer should know before their details are stored, and approved text can replace
ours with no code change.

**Consequences:**

- The statement covers what chat collects and the contact address, and makes no legal claims.
- The estimate notice is always attached by code from `/api/business-information` (`PLATFORM-NOTES.md`
  3.9).
- The statement has not been legally reviewed (`LIMITATIONS.md`).

**Revisit if:** approved privacy text arrives.

---

## D-012: Used-car monthly prices (Q6)

**Date:** 2026-11-01. **Status:** accepted (delegated).

**Context:** `monthlyPricePence` has no term, APR, or deposit. The website still shows "From £X per
month".

**Options:**

- **A.** Show the figure.
- **B.** Show it with the finance notice.
- **C.** Do not show it; quote the cash price and offer a finance enquiry.

**Choice:** C, even though B is the more complex option.

**Why:** more complexity would not help here. A monthly figure without its terms could mislead,
and a notice does not supply the missing terms.

**Consequences:**

- The field never reaches the LLM, and the grounding validator rejects any monthly figure for used
  stock.
- Chat will differ from the website on this. If a customer asks about a monthly figure, chat
  explains that it can't confirm the terms behind it and offers a finance enquiry for a real quote,
  rather than simply declining.
- Published offers are unaffected, because they carry their own terms.

**Revisit if:** the platform supplies full finance terms for used stock.

---

## D-013: Search defaults (Q7)

**Date:** 2026-11-01. **Status:** accepted (delegated).

**Context:** search includes sold cars by default, drops price-on-request cars from budget searches,
and misses plain phrases such as "hybrid SUV" through `q`.

**Options:**

- **A.** Pass searches straight through.
- **B.** Exclude sold, label reserved, and list price-on-request cars separately.
- **C.** B, plus synonym mapping and stepwise relaxation when nothing matches.

**Choice:** C.

**Why:** an empty result is often a vocabulary miss, not missing stock, so stopping there would
fail the brief's natural-language search.

**Consequences:**

- Sold cars appear only when asked for or named directly.
- `availability` takes one value, so one customer search can need several platform queries merged
  in code.
- Relaxation drops one constraint at a time, and chat states which one changed.
- Relaxing is a suggestion, never a silent change to what the customer asked for.

**Revisit if:** query volume or relaxation order produces poor results.

---

## D-014: Test-drive changes (Q8)

**Date:** 2026-11-01. **Status:** accepted (delegated).

**Context:** no API exists to find, amend, or cancel a test drive.

**Options:**

- **A.** Say chat can't help.
- **B.** Offer a message to sales with the reference.
- **C.** B, pre-filled, plus showing test drives booked in this session.

**Choice:** C.

**Why:** the customer gets a real next step, and the session already holds the booking ID needed
to read it back.

**Consequences:**

- A request is a `received` message, not a change, so chat says the dealership must confirm. It
  also gives the dealership's phone number, in case the test drive is soon.
- The original booking stays `confirmed` on the platform, and its slot stays taken.
- Bookings from other sessions cannot be found (`LIMITATIONS.md`).

**Revisit if:** the platform adds test-drive endpoints.

---

## D-015: Website changes (Q9)

**Date:** 2026-11-01. **Status:** accepted (delegated).

**Context:** the vehicle dialog uses `showModal()`, which should make a widget outside it unusable.
This has not been browser-tested.

**Options:**

- **A.** One script tag and no other change.
- **B.** Change the dialog so it no longer opens modally.
- **C.** Leave the dialog alone and move the widget's host element into it while it is open.

**Choice:** C.

**Why:** the website's behaviour stays as it is, and chat works on the page with the most context.

**Consequences:**

- The widget watches the dialog and moves in and out without losing its state.
- Page context is `?vehicle=` plus the current section, and the server treats it as untrusted.
- The move must be proven in a real browser with keyboard and screen-reader checks before it is
  relied on. If it fails, B is the fallback.

**Revisit if:** browser testing shows focus or state problems.

---

## D-016: LLM provider and model (Q10)

**Date:** 2026-11-01. **Status:** superseded by D-019, because no paid key is available.

**Context:** the brief allows an LLM provider, and `CLAUDE.md` names none. Each turn makes up to two
calls, plus regeneration.

**Options:**

- **A.** Claude API with one model.
- **B.** Claude API with a cheaper model for interpretation.
- **C.** A provider-neutral layer over several providers.

**Choice:** A, using `claude-opus-5-5`, behind our own interface, with a scripted fake for
automated checks.

**Why:** B and C are more complex but buy nothing yet. Tiering should follow measurement, and a
second provider adds surface with no customer benefit.

**Consequences:**

- The model is set by environment variable. The key name is `ANTHROPIC_API_KEY`.
- Effort is set explicitly, starting at `low` for both calls, and tuned by measurement.
- Interpretation uses structured outputs through `output_config.format`.
- The server-side refusal fallback is enabled. A remaining refusal counts as an LLM failure (D-006).
- Customer messages reach Anthropic. Verified booking details are withheld.
- Automated checks need no key or network, and without a key the app runs in degraded mode.
- Two calls per turn on Opus may feel slow; this has not been measured.

**Revisit if:** measured latency or cost is too high, in which case test tiering against evals.

---

## D-017: Retention and sessions (Q11)

**Date:** 2026-11-01. **Status:** accepted (delegated).

**Context:** stored chats contain personal data, and the platform's own logs do not redact free
text.

**Options:**

- **A.** Keep chats indefinitely.
- **B.** Anonymous session cookie, 30-day deletion, and logs without message text or contact details.
- **C.** B, plus a customer control that deletes the conversation at once.

**Choice:** C.

**Why:** customers get direct control over their data, and the deletion path already exists for
D-003.

**Consequences:**

- The cookie is HTTP-only and carries no identity.
- Chats are deleted 30 days after last use, at startup and on a periodic job inside the app.
- Operational logs carry IDs and outcomes only.
- Deleting a chat does not delete platform records, and chat says so.
- Text sent to the platform still reaches its request log (`LIMITATIONS.md`).

**Revisit if:** a legal review sets a different retention period.

---

## D-018: Running the product (Q12)

**Date:** 2026-11-01. **Status:** accepted (delegated). Docker is now installed through Colima.

**Context:** the brief requires `docker compose up`, gives the webchat no port, and Docker is not
installed on this machine.

**Options:**

- **A.** Run the webchat separately from `compose.yaml`.
- **B.** Add it to `compose.yaml`, with migrations on start.
- **C.** B, plus a documented source-run path for development.

**Choice:** C, on port 4020.

**Why:** a clean checkout then runs with the brief's one command, and work can continue here before
Docker is installed.

**Consequences:**

- The two existing services stay unchanged in `compose.yaml`.
- Alembic migrations run on start, and SQLite sits on a named volume.
- Docker Desktop was not used. Homebrew has no packages for macOS 27, so Colima, Lima, the Docker
  CLI, Compose, and Buildx came from official releases and sit under the user's home folder, with
  no admin rights needed.
- `docker compose up --build -d` works for the two existing services. The webchat service has yet
  to be added and proven.
- Colima must be running first: `colima start` after each reboot.

**Revisit if:** port 4020 conflicts, or Colima proves unreliable on this macOS version.

---

## D-019: Free LLM (replaces D-016)

**Date:** 2026-11-01. **Status:** accepted (delegated).

**Context:** no paid key is available. The brief forbids requiring any hosted service other than an
LLM provider. This Mac is an M1 Pro with 16 GB of RAM and already has Ollama. `CLAUDE.md` already
keeps prices, availability, hours, and permissions away from the LLM.

**Options:**

- **A. `qwen3.5:9b` on local Ollama.** Free, no key, and customer messages stay on the machine, but
  it is slower and needs about 5.4 GB of memory.
- **B. `qwen2.5:3b` on local Ollama.** Fast, but less accurate on the same test.
- **C. Gemini free tier.** Good quality and no local hardware, but Google's pricing page says
  free-tier content is used to improve its products, which is wrong for customer messages. It also
  needs a key and has changeable limits.
- **D. Groq free tier.** Fast, but it needs a key, has low limits, and sources conflict on data
  retention.

**Measured on this Mac** (15 interpretation and 4 composition cases, a small sample):

| Model | Interpretation | Time per call | Composition | Time per call |
| ----- | -------------- | ------------- | ----------- | ------------- |
| `qwen3.5:9b` | 15/15 | About 5 s | 2/4 | About 2 s |
| `qwen2.5:3b` | 13/15 | About 1 s | 2/4 after a leaked handle is counted as a fail | About 1 s |

**Choice:** A, through Ollama's native API, behind our own interface. C and D were not measured,
because they need a key.

**Why:** customer messages stay private and no account is needed. A wrong interpretation starts the
wrong flow, so accuracy matters more to a customer than 4 extra seconds. The pipeline already makes
a weaker model safe, because code decides, executes, and checks.

**Consequences:**

- Calls use `think: false` and a bounded context. With thinking on, the first call timed out after
  180 seconds.
- Prompts need intent definitions and examples. Without them, both models scored about 3 or 4/15.
- Model name and base URL are environment variables, and no secret is required.
- Composition sometimes drops placeholders, so code-built cards carry the facts and grounding (D-005)
  must catch gaps. Template fallback will be used more than it would with a stronger model.
- A typical turn takes about 7 seconds, plus any regeneration, so progress states matter.
- Ollama runs on the Mac, not in a container, to use the GPU. Containers reach it through
  `host.docker.internal`, which is tested and working with Colima.
- Without Ollama or the model, the app runs in degraded mode (D-006).
- Customers share one local model, so simultaneous chats will queue (`LIMITATIONS.md`).
- A hosted provider would need a new adapter in the interface.

**Revisit if:** a larger evaluation shows poor accuracy, turns feel too slow, or production needs
more customers than one local model can serve.
