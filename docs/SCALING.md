# Scaling

Where the webchat breaks at 10x and 100x concurrent chats, how we would notice, and what each fix
costs. Two fixes are built: the platform gate and cache (section 2), and per-session ordering
(section 3), both chosen because they cost nothing but code (D-024). The repository contract in
section 7 is also in place. Nothing else here is built.

## Summary

| Rank | Bottleneck | Breaks at | Fix cost |
| ---- | ---------- | --------- | -------- |
| 1 | LLM capacity | Now, and hard at 10x | An env change, plus about $25 an hour at 10x on Sonnet |
| 2 | Platform API | 10x | **Built** |
| 3 | Per-session ordering | Any scale | **Built** for one process |
| 4 | SSE connections | 100x, once built | Design time when SSE is built |
| 5 | SQLite | Beyond 100x, or with several processes | 2 to 3 days, thanks to the repository layer |

SQLite was expected to be the first problem, and measurement says otherwise. It is last.

## What the numbers mean

**Load assumptions.** These are estimates, not measured traffic:

| Level | Concurrent chats | Turns a second | Turns an hour |
| ----- | ---------------- | -------------- | ------------- |
| 1x | 5 | 0.17 | 600 |
| 10x | 50 | 1.7 | 6,000 |
| 100x | 500 | 17 | 60,000 |

Each chat is assumed to send one message every 30 seconds.

**Measured** on 2026-11-01, on an M1 Pro with 16 GB of RAM:

| Measurement | Result |
| ----------- | ------ |
| Model turn, local `qwen3.5:9b` | Median 6.8 s, slowest 8.5 s, over 30 turns |
| Uncached prompt reading | About 20 tokens a second |
| SQLite writes, 1 and 8 writers | About 1,550 a second, p99 under 6 ms |
| SQLite writes, 32 writers | 1,484 a second, p99 474 ms, no errors |
| SQLite writes, 64 writers | 1,184 a second, p99 1,200 ms, no errors |
| Platform, 5 concurrent reads | 291 a second, none failed |
| Platform, 50 concurrent reads | 288 a second, 63 of 600 failed |
| Platform, 200 concurrent reads | 289 a second, 265 of 1,200 failed |
| Platform, 500 concurrent reads | 1,083 of 1,500 failed |

**Limits of this evidence:**

- SQLite writers were threads in one Python process, with one small row each.
- The platform ran from source on macOS with Python 3.11, not in its Docker image.
- SSE now exists, but has not been load tested, so section 4 is reasoning, not measurement.
- Costs use Anthropic list prices cached on 2026-10-06 and about 1,500 input and 120 output tokens a
  turn. That token count is estimated from prompt sizes, not read from real usage data.

**Metrics.** None of the metric names below exist yet. Today we have the event log, uvicorn access
logs, and the circuit breaker's state.

## 1. LLM rate limits and cost

**Symptom.** One local model handles about 9 turns a minute, and 1x already needs 10.

- At 10x, the queue exceeds the 20-second limit, so most turns time out.
- The breaker then opens, and customers get buttons with no free-text chat.
- At 100x, a free hosted tier fails too. Published limits vary, but about 30 requests a minute gives
  roughly 15 turns a minute, against the 1,000 a minute we would need.

The failure is graceful: nothing is lost, and buttons and confirmations keep working. It still means
the "AI webchat" has stopped being one.

**Detect.**

- `llm_call_seconds` by stage, watching p95 against the 20-second limit
- `llm_failures_total` by kind, especially `timeout` and `rate_limited`
- `llm_breaker_open_seconds`, and the share of turns with `degraded=true`
- `llm_tokens_total`, compared with spend

**Fix, in order.**

1. Point `LLM_BASE_URL` at a paid provider. No code changes, because the adapter already handles any
   OpenAI-compatible endpoint.
2. Cut calls: skip interpretation for button presses, which already happens, and for form steps.
3. Use prompt caching where the provider offers it. The interpretation prompt is mostly fixed.
4. At 100x, keep a second provider or model for failover.

**Cost.**

| Model | Per turn | 10x, per hour | 100x, per hour |
| ----- | -------- | ------------- | -------------- |
| Haiku 5.5 | $0.0002 | $1.27 | $13 |
| Sonnet 5.5 | $0.004 | $25 | $255 |
| Opus 5.5 | $0.008 | $51 | $509 |

These are peak rates, assuming every chat stays active for the full hour. A hosted model also means
customer messages leave the machine, which reverses the main reason for D-019. That needs a privacy
decision, not just a budget.

## 2. Platform API

**Symptom.** The platform copes with volume but not with simultaneous connections.

- Throughput holds at about 290 requests a second. At 50 concurrent, 10% of requests failed.
- The likely cause is a listen backlog of 5 and a new connection per request, so bursts are refused.
- A stock search needs 3 or more reads, and a budget search about double that.
- The client's retries absorb some of this, but they add load during the worst moment.
- At 100x, the average of about 70 requests a second looks fine, and bursts are what will hurt.

We cannot change the platform, so all of this must be solved on our side.

**Detect.**

- `platform_requests_total` by endpoint and result
- `platform_transport_errors_total`, an early warning, since connection errors arrive first
- `platform_request_seconds` p95 and p99
- `dealership_retries_total`
- `write_outcome_unknown_total`, the signal that matters most to customers

**Fix.**

1. Add a concurrency gate in `DealershipClient`, with a limit of about 10 requests at once. Waiting
   briefly in our queue is far better than a refused connection.
2. Add a short read-through cache for catalogue reads: 30 to 60 seconds for vehicles, and 5 minutes
   for hours, offers, and service types.
3. Never cache availability checks before a write, or slot lookups in the recheck. Those must stay
   live, because W3 depends on it.

**Built (D-024).** The gate limits the client to 10 requests at once, and releases its place during
retry backoff. Vehicle reads are cached for 30 seconds, and hours, offers, dealerships, service types,
and business information for 5 minutes. Availability, slots, and bookings are never cached, and
pre-write hours checks skip the cache. Result on the real platform, for a burst of 200 different
searches with retries turned off:

| Setup | Failed | Time |
| ----- | ------ | ---- |
| No gate | 26 of 200 | 0.70 s |
| Gate of 10 | 0 of 200 | 0.51 s |
| Gate of 10, 200 identical searches | 0 of 200 | 0.01 s |

**Cost.** No money. A cached listing can be up to 30 seconds stale, so a card may briefly show a car
as available after it changes. That is safe because every write rechecks live data. The cache and
gate are per process, so several processes would each have their own.

## 3. Per-session ordering

**Symptom.** This one is about correctness rather than load. D-001 stage 1 promised a per-session
lock that was not built until D-024. Without it:

- Two tabs, or a quick second message, can run the same session's turns at once.
- Replies can arrive out of order, and a later turn can read state from before an earlier one ended.
- A workflow can start while another turn is still interpreting.

Tokens stay safe, because `apply` serialises state changes, so this cannot cause a double booking.
The damage is confusing conversations, and more concurrency makes it more frequent.

**Detect.**

- `turn_overlap_total`, counting a turn that starts while another is running for that session
- `turn_queue_seconds`, once a lock exists
- Event log review: `seq` gaps between one turn's start and end entries

**Built (D-024), for one process.** `TurnEngine.message` and `action` take a per-session lock, in
arrival order, with up to 3 turns waiting. A further turn gets an honest "still working" reply and
logs `turn_busy`. Locks are removed when idle. Confirmations do not wait behind it, because they are
already safe and a click should never queue behind a slow model.

**Still to do.** Several processes would need a lease row in the repository, with an expiry so a
crash cannot hold it for ever. Form fields and identity submissions do not take the lock, because
`apply` already serialises them.

**Cost.** No money. Leases would be about another day.

## 4. SSE connection limits

**Symptom.** SSE is built but not load tested, so this is what to expect, not what we have seen.

- An idle async connection is cheap, so 500 should fit in one uvicorn process.
- File descriptor limits are the first wall: 256 by default on macOS, and often 1,024 on Linux.
- Browsers allow about 6 HTTP/1.1 connections per site, so several tabs can starve each other.
- Proxies may buffer events or close idle streams, which looks like a frozen chat.
- Slow clients could hold memory if we buffer for them without a limit.

**Detect.**

- `sse_connections_open`, compared with the file descriptor limit
- `sse_disconnects_total` by reason
- `sse_resume_total`, and how many resumes fail
- `sse_time_to_first_event_seconds`

**Fix.** A stream lasts one turn, with a heartbeat every 15 seconds and resume from `Last-Event-ID`,
so idle chats hold no connection, which removes most of this risk. Still to do at scale:

- `ulimit -n` of at least 4,096, and HTTP/2 at any proxy
- Replacing the 100 ms database poll behind resume, if resumes become common

**Cost.** A small configuration change. At 100x, more than one process means a shared pub/sub layer,
and the brief forbids requiring another hosted service. That would be a real decision.

## 5. SQLite write contention

**Symptom.** At our load, this is not a bottleneck.

- 100x needs roughly 85 writes a second, about 6% of what we measured.
- With one process, writers cannot really contend, and p99 stays under 6 ms.
- The first real limit is process count. At 32 writers, p99 reached 474 ms, and `BEGIN IMMEDIATE`
  means every write waits for the one before it.
- Writes are synchronous, so each blocks the event loop briefly. At 100x that is around 4% of the
  loop's time, and it will spike during slow disk moments.
- `last_outcome` reads a session's whole log. It took 1.8 ms at 500 events, so it is fine for now.

**Detect.**

- `db_apply_seconds` p99, with an alert above 50 ms
- `db_busy_waits_total`
- `event_loop_lag_seconds`
- WAL file size, and database size against the 30-day retention

**Fix, smallest first.**

1. Move `apply` onto a thread pool, about 2 hours, if event loop lag appears.
2. Index outcome rows and stop reading the full log, about 2 hours.
3. Move to Postgres when running several processes.

**Cost.** Postgres takes about 2 to 3 days, thanks to the repository contract: a new `Repository`
implementation, `SELECT ... FOR UPDATE` in `apply`, and running the existing suite against it. It
also adds a service to run, and the brief says nothing but an LLM provider may be a required hosted
service, so it would have to run locally or stay optional.

## 6. What was picked

The user asked for whichever fixes are free of cost. These two were recommended and are built:

1. **Platform concurrency gate and read cache (section 2).** It is the only bottleneck already failing
   real requests at 10x, it cannot be fixed anywhere else, and it reduces unknown-write outcomes,
   which are our worst customer experience.
2. **Per-session ordering (section 3).** It is a promised behaviour that is missing, it causes visible
   bugs at any scale, and it costs half a day.

**Why not the LLM,** when it is the biggest bottleneck? Its fix is a decision rather than code: a
provider, a budget, and whether customer messages may leave the machine. Changing it is already just
an environment change, and I recommend making that decision separately.

**Why not SQLite?** It has the most headroom, and swapping it is now cheap. Moving early would add a
service to run and gain nothing.

## 7. Repository layer

The service depends on the `Repository` protocol in
`webchat/src/northstar_webchat/persistence/repository.py`, not on SQLite.

- `tests/writes/test_repository.py` runs a real booking against the real platform on a second,
  in-memory implementation.
- A test fails if anything outside `persistence/` and `migrations/` imports SQLAlchemy.
- Any replacement must keep two rules: `apply` is atomic and serialised per session, and a stored
  request is durable before `apply` returns.
