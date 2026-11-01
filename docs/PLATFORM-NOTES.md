# Dealership platform notes

Pre-design notes on what the dealership platform really does, compared with what its docs and
the product brief say.

**Sources:** `PRODUCT-BRIEF.md`, `docs/INTEGRATION-GUIDE.md`, `docs/BUSINESS-SEMANTICS.md`,
`docs/SEEDED-SCENARIOS.md`, the OpenAPI contract, and the platform source.

**How this was verified:** the unmodified platform was run from source and exercised over HTTP on
2026-11-01. Its `/openapi.json` is identical to the repo copy. The full probe used Python 3.11 on
macOS. Afterwards, the key findings were spot-checked on the real `python:3.12-alpine` image and
matched: the released-slot 500, the estimate replay losing its notice, sold cars showing a next
slot, and the keyless admin summary.
Behaviour below was observed live unless marked *(source only)* or *(not browser-tested)*.
Absolute dates come from that run; seeded dates move with each reset.

---

## 1. Endpoints

The contract has 30 operations: 13 public, 17 needing `X-API-Key`. The key is required for every
write and for every read of a saved customer record. It must stay server-side.

### Public: no key

| Method | Path | Used for |
| ------ | ---- | -------- |
| GET | `/api/vehicles` | Stock search, refinement, comparison |
| GET | `/api/vehicles/{vehicleId}` | Full details; only source of `highlights` |
| GET | `/api/vehicles/{vehicleId}/availability` | Current state and permitted next actions |
| GET | `/api/offers` | Published new-car offers (filters: `make`, `productType`) |
| GET | `/api/offers/{offerId}` | One offer |
| GET | `/api/dealerships` | Addresses, contact details, brands |
| GET | `/api/dealerships/{dealershipId}` | One dealership |
| GET | `/api/dealerships/{dealershipId}/opening-hours` | Weekly hours and holiday exceptions |
| GET | `/api/service-types` | Workshop services, duration, "from" price |
| GET | `/api/workshop-locations` | Same payload as `/api/dealerships`; adds no workshop data |
| GET | `/api/workshop-availability` | Workshop slots |
| GET | `/api/test-drive-slots` | Test-drive slots |
| GET | `/api/business-information` | Finance and part-exchange notices, privacy contact |

Also public but outside the contract: `GET /health` (useful for the chat's "unavailable" state)
and `GET /assets/vehicles/{id}.jpg`.

### API key required

| Method | Path | Idempotency-Key | Used for |
| ------ | ---- | --------------- | -------- |
| POST | `/api/test-drive-bookings` | **Required** | Book a test drive |
| POST | `/api/workshop-bookings` | **Required** | Book the workshop |
| POST | `/api/sales-enquiries` | Recommended | General, availability, finance, part-exchange enquiry |
| POST | `/api/vehicle-interests` | Recommended | Register interest in a reserved vehicle |
| POST | `/api/callback-requests` | Recommended | Callback for sales, service, or parts |
| POST | `/api/dealership-messages` | Recommended | Message for sales, service, parts, or general |
| POST | `/api/part-exchange-valuations` | Recommended | Indicative estimate |
| POST | `/api/workshop-bookings/lookup` | Ignored | Identity-checked booking retrieval |
| PATCH | `/api/workshop-bookings/{recordId}` | Ignored | Amend slot, mileage, or notes |
| DELETE | `/api/workshop-bookings/{recordId}` | Ignored | Cancel |
| GET | `/api/workshop-bookings/{recordId}` | n/a | Re-read after amend; lacks time and names |
| GET | `/api/test-drive-bookings/{recordId}` | n/a | No planned use |
| GET | `/api/sales-enquiries/{recordId}` | n/a | No planned use |
| GET | `/api/vehicle-interests/{recordId}` | n/a | No planned use |
| GET | `/api/callback-requests/{recordId}` | n/a | No planned use |
| GET | `/api/dealership-messages/{recordId}` | n/a | No planned use |
| GET | `/api/part-exchange-valuations/{recordId}` | n/a | No planned use; omits the estimate notice |

`PATCH` and `DELETE` take the internal `id` (for example `wsb-seeded-001`). The customer's
reference returns 404, so the path to either one runs through a lookup.

**Do not use:** `/admin/api/summary` and `/admin/api/reset`.

### Field values the contract leaves open

- `fuelType`: `Petrol`, `Diesel`, `Hybrid`, `Electric`
- `bodyStyle`: `SUV`, `Hatchback`, `Saloon`, `Estate`
- `transmission`: `Automatic` only; all 60 vehicles are automatic
- Most success bodies are typed as "any object". Our models must be written from observed
  responses, not generated from the contract.

---

## 2. Where a successful response is not business success

The platform has no literal `pending`, `waitlisted`, or `partial` status. The status values are
`confirmed`, `received`, `requested`, `registered`, `estimated`, and `cancelled`. Of these, only
`confirmed` commits the dealership to anything, and even that has exceptions below. Closest
equivalents: `requested`/`received` behave like pending, and `registered` behaves like a waitlist
without a queue.

### Writes

| Response | What it means | What it does not mean |
| -------- | ------------- | --------------------- |
| Sales enquiry: 201 `received` | Details were saved | Any reply time, finance approval, or vehicle availability. Accepted for sold vehicles, and when the vehicle belongs to a different dealership from the one sent |
| Callback: 201 `requested` | Dealership has the request | Any callback time. `preferredTime` is unvalidated text; "3am on a Sunday" was accepted |
| Message: 201 `received` | The department can inspect it | That anyone has replied |
| Interest: 201 `registered` | Interest recorded | A queue place or future availability. The reference is `WAIT-…`, which reads like a waitlist, so it must not be described as one |
| Valuation: 201 `estimated` | An indicative range and a stored record containing contact details | An offer. "ZZ" with 999,999 miles still got £1,250 to £2,150, so the registration is not checked against any real vehicle |
| Any create with `idempotentReplay: true` | The earlier record was returned | A new action. Replays also return 201, so status alone cannot tell them apart |
| Workshop replay: 201 `cancelled` | The booking has since been cancelled | A live booking. Replay returns the current row, not the original: 201 with `status: cancelled` was observed |
| Test-drive or workshop: 201 `confirmed` | The slot is held | That the customer saw the right thing. The body has no appointment time, service name, or dealership name, so we must carry those from the chosen slot |
| Test-drive `confirmed` | Vehicle comes from the slot | That it is the vehicle under discussion. The request has no `vehicleId`, so a stale slot books a different car |
| Workshop `confirmed` | Slot claimed | That the time is in the future. A past-dated slot was listed and booked. No opening-hours check exists either (see 3.8) |
| Amend: 200 | The booking changed | That it stayed at the same place or service. A move from a Manchester MOT slot to a Liverpool diagnostic slot returned 200 and silently changed both. The body has no appointment time |
| Cancel: 200 `cancelled` | Booking is cancelled | That this call cancelled it. A repeat returns the same record, including the original `cancelledAt` |
| Cancel: slot "released" | The slot reappears in availability | That anyone can book it. See 3.7 |

### Reads

| Response | Trap |
| -------- | ---- |
| Lookup: 200 | Status may be `cancelled`, with the old appointment time still present. Check status before saying the customer has a booking |
| Lookup: 200 | Also returns full name, email, phone, mileage, and free-text notes. Strip these before anything reaches the LLM |
| Availability: 200 | `nextTestDriveSlot` is populated for reserved and sold vehicles. `canBookTestDrive` is the only safe signal |
| Test-drive slots: 200 | Reserved and sold vehicles have listed `available` slots: `veh-007` has 4, `veh-013` has 4, `veh-037` has 5 |
| Vehicle search: 200 | Includes sold and reserved vehicles unless filtered |
| Vehicle search: 200 with no items | May be a vocabulary miss, not missing stock. `q` covers only make, model, variant, and colour, as one contiguous phrase. `q=SUV`, `q=hybrid`, `q=x3 bmw`, `model=Evoque`, `make=Range Rover`, and `fuelType=EV` all return nothing, despite matching stock |
| Vehicle search with a price filter | `veh-019` and `veh-046` silently disappear, because their price is null |
| Any `availability` value in stock results | A snapshot only. It can change before the operation runs |
| Slot lists: 200 with no items | Three cases look identical: real absence, an unknown or wrong-case ID, and an unparseable date such as `dateFrom=tomorrow`. Offers do the same with `productType=bogus` |
| Slot lists: 200 | Silently capped at 100 with no pagination. Unfiltered requests are truncated |
| Slot lists: 200 | No lower date bound; past slots stay listed on an ageing dataset |
| Opening hours: 200 | `weekly` does not account for `holidayExceptions`. The two must be combined per date |
| Search: bad `sort`; `pageSize=0` or `500`; `page=999` | All 200: the sort falls back, page size is clamped, and the page is empty |

### Failures that are normal, or not what they claim

- `VEHICLE_RESERVED`, `VEHICLE_UNAVAILABLE`, `VEHICLE_NOT_RESERVED`, `SLOT_UNAVAILABLE`,
  `BOOKING_NOT_FOUND`, and `BOOKING_CANCELLED` are expected business outcomes that need a next
  step, not an error screen.
- `retryable` is not a recovery guide. `SLOT_UNAVAILABLE` says `false`, yet the right move is to
  offer another slot. The only code that says `true` is `INTERNAL_ERROR`, including the permanent
  failure in 3.7. Retrying that one never succeeds.
- Connection failures on a write leave the outcome unknown. Three of 25 concurrent booking
  requests failed at connection level here, and no double-booking occurred. Retries of creates
  must reuse the same key and an identical body.
- The idempotency hash uses the raw body. A retry that reformats the phone number
  (`07700 900999`) gets `IDEMPOTENCY_CONFLICT`, so keep the exact first body for retries.

---

## 3. Seeded scenarios that look like traps

The docs describe only some of these. Items marked † are not in `SEEDED-SCENARIOS.md`; they come
from the seed code or from observed behaviour.

### 3.1 Vehicle state

| Scenario | Seeded data | Trap | Brief requirement | Correct behaviour |
| -------- | ----------- | ---- | ----------------- | ----------------- |
| Reserved | `veh-007`, plus `veh-028`† and `veh-054`† | Slots are listed, booking gives 409 | "understand whether a vehicle is available, reserved, or sold"; "register interest when a vehicle is currently reserved" | Offer interest and enquiry; never offer slots |
| Sold | `veh-013`, plus `veh-037`† | Slots are listed; interest is rejected, enquiry accepted | Same; "ask whether a vehicle is available" | Say sold; offer enquiry or alternatives |
| Available vs reserved vs sold lookalikes† | `veh-013` is sold and `veh-049` is not, both BMW 1 Series Fire Red. `veh-028` is reserved and `veh-052` is not, both BMW i4 Alpine White | "The red 1 Series" matches more than one state | "ask whether a vehicle is available"; "compare relevant vehicles" | Disambiguate by ID, year, mileage, or location before answering |
| Price on request | `veh-019`, plus `veh-046`† | Null price and null monthly price; dropped by any budget filter; `veh-046` matches priced `veh-022` on make, model, and colour | "view the important details"; CLAUDE.md: LLM never supplies prices | Say price on request; never estimate |
| Stale state | "Availability may change" | Nothing in the API can change a vehicle's state, so this cannot be reproduced live | "recover gracefully" | Re-check before writes; handle 409s; cover with mocked tests |

### 3.2 Discovery and offers

| Scenario | Seeded data | Trap | Brief requirement | Correct behaviour |
| -------- | ----------- | ---- | ----------------- | ----------------- |
| Natural search† | Body and fuel are not searched by `q` | "Hybrid SUV" through `q` returns zero | "search stock using natural descriptions" | Code maps preferences to structured filters |
| Model names† | Evoque's model is "Range Rover Evoque", make "Land Rover" | Exact-match `model` and `make` miss it | Same | Use `q` for names; retry fuzzy spellings such as "fpace" |
| Ambiguous names† | `q=Cooper` returns 10: 5 Coopers and 5 Countrymans (variant "Cooper Classic"). 12 models × 5 each | Picking the first hit | "refine or change requirements"; "compare" | Show the matches or ask |
| Manual request† | All 60 are automatic | Zero results looks like a failure | "search stock" | State plainly that no manuals are in stock |
| Brand vs location† | 15 vehicles are at a site whose `brands` omits the make, such as a BMW at Liverpool | Telling a customer a site doesn't have a car it does | "find relevant vehicles" | Use a vehicle's `dealershipId`, never `brands`, for stock |
| Monthly price† | Used stock has `monthlyPricePence` with no term, APR, or deposit | Reads like a finance quote | "accurate finance… notices"; "without inventing prices or terms" | See Q6 |
| Offer gaps† | No Volvo or Kia offers | Empty result invites invention | "published new-car offers without inventing prices or terms" | Say none are published |
| Offer terms† | PCH offers have `apr: null`; `expiresOn` is present but not filtered on; offers end 90 days after seeding | Stating an APR for PCH; quoting an expired offer | Same | Use stated fields only; check expiry in code |

### 3.3 Hours and holidays

| Scenario | Seeded data | Trap | Brief requirement | Correct behaviour |
| -------- | ----------- | ---- | ----------------- | ----------------- |
| Bank holiday | Seed day +30 at all sites (2026-12-01 in this run, a Tuesday). Sales 10:00–16:00, service closed | The same weekday reads as normal in `weekly` | "understand holiday opening-hour exceptions" | Resolve hours per date, with exceptions first. Do not hardcode the date, and do not name the holiday: only the label "Bank holiday" exists |
| Parts on the holiday† | Parts has no exception row | Implying the whole site is closed, or asserting parts is definitely open | Same | Report the weekly hours and say no exception is listed |
| Holiday on a Sunday† | Depends on the reset date | Exception says open on a closed day | Same | The exception wins *(source only)* |
| Department differences | Sat: sales 09:00–17:00, service and parts 09:00–13:00. Sunday closed | Quoting one set of hours for a whole dealership | "opening hours" | Answer per department |
| Holiday vs bookings† | Slots end 10 days before the holiday | Holiday booking cannot be demonstrated from the seed | Same | Test with mocked slots |
| Branch name† | Bolton's address is 77 Manchester Road | "Manchester Road branch" is Bolton, not Manchester; Stockport and Manchester share 0161 | "find dealership addresses" | Confirm the branch before writes |

### 3.4 Workshop availability

| Scenario | Seeded data | Trap | Brief requirement | Correct behaviour |
| -------- | ----------- | ---- | ----------------- | ----------------- |
| Bolton | Blocked for 7 days (first open slot 2026-11-09 here) | Empty looks like an outage | "suitable workshop locations"; "make a new workshop booking" | Say none in that window; offer later dates or another site |
| Service by location† | Each site offers 4 fixed services at 4 fixed times (table below) | `/api/workshop-locations` implies every site does everything. No MOT at Liverpool or Stockport; recall only at Stockport | "discover supported service types and suitable workshop locations" | Derive suitability from availability |
| Price edge cases† | `tyre-fitting` price null; `recall` price 0 | "£0" or "free" invented for null; "from" dropped | "discover supported service types" | Null means price on request; keep "from" |

Seeded service by start time (UTC), identical every working day:

| Site | 09:00 | 11:00 | 14:00 | 16:00 |
| ---- | ----- | ----- | ----- | ----- |
| Manchester | Full service | MOT | Service and MOT | Diagnostic |
| Stockport | Tyre fitting | Recall | Interim | Full service |
| Liverpool | Service and MOT | Diagnostic | Brake inspection | Tyre fitting |
| Bolton | Interim | Full service | MOT | Service and MOT |

### 3.5 Identity verification

| Scenario | Seeded data | Trap | Brief requirement | Correct behaviour |
| -------- | ----------- | ---- | ----------------- | ----------------- |
| Happy path | `WORK-10001` Taylor, `AB12 CDE`, `07700900123`; `WORK-10002` Morgan, `XY34 ZZZ`, `07700900456` | Over-strict matching in our layer | "retrieve the details and status… after verifying the customer's identity" | Forward values; the platform accepts lower case, no-space registrations, and `+44`/spaced phones |
| Which value is wrong | Each single mismatch gives the same 404 | Revealing which factor failed | Same; "securely and responsibly" | Generic "couldn't match those details" |
| Format errors† | `AB12-CDE` and `123` give 422 with field names; full name or first name gives 404 | A format error is not a mismatch; it is fine to ask for a corrected format | Same | Strip hyphens first; keep 404 generic; ask for surname only |
| Cross-customer | Taylor's reference with Morgan's details | Partial verification leaking data | Same | All four values or nothing |
| Brute force† | 60 consecutive failures, no lockout; correct details then succeed | The platform has no attempt limit | "securely and responsibly" | Limit attempts ourselves (Q2) |
| Unverified writes† | `PATCH`/`DELETE`/`GET` need only the shared key and booking ID | The platform will amend or cancel any booking; the identity gate exists only in our code | "amend… cancel…"; CLAUDE.md: LLM never decides what is allowed | Only act on a booking ID verified in this session, held server-side (Q2) |
| Cancelled booking | Lookup still returns it | Reporting a live appointment | Same | Show the cancelled status |
| Stored prompt injection† | `notes` and `message` are customer text returned by the platform | Treating stored text as instructions | "securely and responsibly" | Treat as data; keep notes away from LLM context where not needed |

### 3.6 Booking and amendment

| Scenario | Seeded data | Trap | Brief requirement | Correct behaviour |
| -------- | ----------- | ---- | ----------------- | ----------------- |
| Concurrent claim | Any slot | Two customers, one slot. Only one of 25 succeeded | "recover gracefully" | Re-offer slots on 409; never report success early |
| Duplicates† | Optional-key writes | Without a key, a repeated interest created a second record | "recover gracefully from failed or interrupted requests" | Always send a key |
| Move across services† | `PATCH` slot from another site or service | Silent change of both | "amend an existing workshop booking" | See Q3 |
| What can be amended† | `slotId`, `mileage`, `notes` only | Promising to change registration or contact details | Same | Explain; route to a message |
| Failed move | Taken or blocked slot gives 409 | Telling the customer it moved | Same | Booking is unchanged; tell them so |
| Cancelled then amended† | 409 `BOOKING_CANCELLED`, undocumented | Generic error | Same | State that it is cancelled; offer a new booking |
| No test-drive changes† | No lookup, amend, or cancel endpoint | Promising to change a test drive | "make a confirmed booking" | See Q8 |
| Contact formats† | `020` and `03` numbers and `0044` prefix are rejected; one-letter first names and digits too | Valid UK customers blocked | "complete common sales and service tasks" | Relay `fieldErrors`; normalise `0044`; ask for an 01 or 07 number |

### 3.7 Cancelled workshop slots cannot be rebooked†

The most serious finding. Cancellation reports the slot as released and it reappears in
availability, but the cancelled row keeps its `slot_id`, and `workshop_bookings.slot_id` is unique
(`dealership-platform/src/database.py:173`).

- Booking a released slot: **500 `INTERNAL_ERROR`, `retryable: true`**, every time.
- Moving another booking into it: same 500, booking unchanged.
- Seeded `WORK-10001`, once cancelled, poisons `ws-slot-0001` the same way.
- Slots vacated by an *amend* are fine: rebooking one returned 201.
- Server log: `UNIQUE constraint failed: workshop_bookings.slot_id`.

Scenario: a customer cancels, changes their mind, and asks for the same slot back in the same
conversation. **Brief:** "cancel an existing workshop booking"; "recover gracefully". We cannot fix
the platform. See Q1.

### 3.8 Time†

| Scenario | Seeded data | Trap | Brief requirement | Correct behaviour |
| -------- | ----------- | ---- | ----------------- | ----------------- |
| UTC display | Slots at 09/11/14/16Z. No difference today; UK is on GMT until late March 2027 | Correct in winter, an hour wrong in summer. A summer 16:00Z is 17:00 UK, and tests run now will not catch it | "accurate dealership information" | See Q4 |
| Slots outside hours | Saturday service closes 13:00, yet 22 of 44 Saturday workshop slots start 14:00 or 16:00 | Booking the customer into a closed department | "accurate dealership information" | See Q4 |
| Jobs overrunning | 64 of 262 workshop slots end after closing, such as Service and MOT at 16:00 | Implying collection the same day | Same | State the start time only; do not promise completion |
| Ageing data | Dates fixed at first seed; not refreshed on restart | Old slots still bookable; seeded bookings (first working day) soon lie in the past | "make a new workshop booking" | Filter past slots; `dateFrom` is always ours to set |
| Reset | `./reset.sh` wipes records and idempotency keys | Stored conversations reference records that no longer exist | "preserve a customer's conversation" | Treat 404 on a stored ID as gone; do not crash |
| `dayOfWeek` | Monday is 0 | Off-by-one if read as Sunday-first | "opening hours" | Use `day` or map explicitly |

### 3.9 Part exchange and notices

| Scenario | Seeded data | Trap | Brief requirement | Correct behaviour |
| -------- | ----------- | ---- | ----------------- | ----------------- |
| Lost qualification† | `estimateNotice` is on the first response only. Replays and `GET` omit it | The retry path shows a bare number (`application.py:963-971`) | "indicative part-exchange estimate with the applicable qualification" | Always attach the notice from `/api/business-information` in code |
| Contact needed first† | Estimate needs name, email, and phone; 422 otherwise | Quietly collecting and storing PII for a number | Same; "securely and responsibly" | See Q5 |
| Repeat estimates† | Same input gives the same range but a new record and reference | Duplicate records | Same | Key each confirmed request |
| Finance notice | `finance.notice`, `minimumAge: 18` | Paraphrased or omitted; LLM-invented terms | "accurate finance, privacy, and part-exchange notices" | Render platform text verbatim from code |
| Privacy "notice"† | Only `privacyContact`, an email address | Inventing a policy | Same | See Q5 |

---

## 4. Contradictions

### Brief vs platform

1. **Privacy notice:** the brief wants an "accurate" privacy notice, and the contract says
   business information has privacy notices. The platform has only a contact address.
2. **Amend and cancel identity:** the brief requires verification explicitly only for retrieval.
   The platform requires it for nothing except lookup, yet amending or cancelling without it would
   fail "securely and responsibly".
3. **Fix nothing vs broken flow:** the brief forbids changing the platform, while cancellation
   leaves unusable slots (3.7). The workaround must live in our code.
4. **Ports:** the brief says services run "on their documented ports" but gives the webchat none.
5. **Callbacks:** the brief lists "request a callback" generally. Callbacks accept only sales,
   service, or parts; `general` is message-only.
6. **Site structure:** the brief wants page context and usable links, but the nav's `#offers` is a
   brand-promise section and `#service` is the footer. Only vehicles have a stable link, so offers
   and services have nothing to link to.
7. **Modal:** the brief wants chat "available throughout the website". Vehicle details open with
   `showModal()`, which normally makes the rest of the page inert, so a widget outside the dialog
   would be unusable on the page with the most context *(not browser-tested)*.

### Docs vs docs

8. **Idempotency on claims:** semantics says a key is "mandatory when claiming" a workshop slot,
   but `PATCH` claims one and accepts no key.
9. **Slots vs hours:** the seeded slots and seeded opening hours disagree on Saturdays (3.8).
10. **Timestamps:** "all timestamps are UTC", but opening hours are bare `HH:MM` with no stated
    zone. No document states the dealership's timezone.
11. **Key handling:** docs say never to expose the key in browser code, but the platform's own
    `docs.js` and `admin.js` ship the API and admin keys to the browser. README says "server-side
    code"; the guide says "server-side configuration".
12. **Error table:** six real codes are missing: `VEHICLE_NOT_RESERVED`, `BOOKING_CANCELLED`,
    `IDEMPOTENCY_KEY_REQUIRED`, `INVALID_IDEMPOTENCY_KEY`, `INVALID_JSON`, `REQUEST_TOO_LARGE`.

### Docs vs behaviour

13. **"Releases the slot":** see 3.7.
14. **"Returns the original record":** replay returns the current row, including `cancelled`, and
    drops `estimateNotice`.
15. **Estimates "include the applicable qualification":** only on the first response.
16. **"Saved customer-record reads… require `X-API-Key`":** `GET /admin/api/summary` returned
    customer names, emails, and phones with no key, and CORS is `*`.
17. **"The request log redacts customer identity and contact details":** only named fields are
    redacted. A phone number and name typed into `message` were logged verbatim. Free text from
    chat will reach this log.
18. **"Relative to the day it is started":** dates are set at first seed or reset. Restarting with
    the existing volume does not reseed *(source only)*.
19. **"Permitted next actions":** `nextTestDriveSlot` is returned when no test drive is permitted.
20. **"Complete updated booking":** an amend omits the appointment time and all display names.
21. **"retryable":** `true` on a failure that can never succeed (3.7).
22. **Contract status codes omitted:** 401 on every keyed operation except `POST
    /api/sales-enquiries`; 400 on bookings; 404 on `PATCH` and on creates with an unknown
    dealership, vehicle, or slot; 409 `IDEMPOTENCY_CONFLICT` on enquiry, callback, message, and
    valuation; 413 and 500 everywhere.
23. **Contract constraints omitted or wrong:** enums for `sort` and `pageSize` bounds are not
    enforced. Undocumented limits: subject 3–120, message 5–2000, `reason` max 1000,
    `preferredTime` max 120, registration `^[A-Z0-9 ]{2,10}$` after upper-casing, mileage max
    1,000,000, names may not contain digits.

---

## 5. Questions before design

Kept as asked. The answers are D-007 to D-018 in `docs/DECISIONS.md`, and some go beyond the
recommendation given here.

**Q1. How should we handle a slot that cannot be rebooked after cancellation?**
(a) Treat a 500 on a booking write as "that slot can't be booked", offer others, and don't retry;
(b) also hide slots cancelled through our chat before the customer sees them; (c) warn before
cancelling. **Recommend (a) + (b)**, plus a `LIMITATIONS.md` entry. Is there anyone to report this
to, or is it fixed for this project?

**Q2. What is the verification policy for retrieve, amend, and cancel?**
Recommend: the same four-factor lookup for all three; the verified booking ID held server-side
and bound to the session and one booking; expiry after 15 minutes; a new check per booking; a
lockout after 5 failures per session; explicit customer confirmation before every write. Are those
limits acceptable, and should verification survive a page refresh inside the 15 minutes?

**Q3. How far may an amendment go?**
(a) Same service and site only; (b) same service, any site, with explicit confirmation of the
change; (c) anything the platform allows. **Recommend (b).** A service change would be a cancel
plus a new booking.

**Q4. How should slot times be treated?**
(a) Are slots truly UTC, or UK wall-clock hours stored with a `Z`? **Recommend following the docs:
convert to Europe/London.** (b) Should chat offer workshop slots starting outside service hours,
which is 22 of 44 Saturday slots? **Recommend hiding those that start outside hours and logging
it**, but it means chat shows fewer slots than the platform.

**Q5. What are the part-exchange and privacy rules?**
The platform needs name, email, and phone before it gives an estimate. **Recommend:** explain why,
show the privacy contact, and get explicit confirmation first. Do you have approved privacy text?
If not, I recommend a factual statement of what chat collects plus the contact address, with no
legal claims, and a `LIMITATIONS.md` entry.

**Q6. Should chat show used-car monthly prices?**
The website shows "From £X per month", but the data has no term, APR, or deposit. **Recommend not
showing them**: quote the cash price and offer a finance enquiry, with the finance notice. This
will differ from the website.

**Q7. What are the search defaults?**
**Recommend:** exclude sold vehicles unless asked for or named directly; include reserved vehicles,
clearly labelled; and on budget searches, list price-on-request cars separately rather than losing
them. Does that match what you want?

**Q8. What happens when a customer wants to change or cancel a test drive?**
No API exists. **Recommend** saying chat can't change it, then offering a message to sales with the
`TEST-…` reference. A record of "cancelled" would not exist on the platform, so the customer must
be told the dealership needs to confirm.

**Q9. How much may the website change?**
It's editable. **Recommend:** one script tag, plus a change so the chat works while the vehicle
dialog is open, such as rendering the widget inside the dialog's top layer or changing how the
dialog opens. Page context would be `?vehicle=` and the current section. Is that within bounds?

**Q10. Which LLM provider and model?**
`CLAUDE.md` names none. Will a key be available for local running? **Recommend** the Claude API,
behind our own interface, with a scripted fake so automated checks need no key or network. Customer
messages will reach the provider, so please confirm that's acceptable; verified booking PII would
be withheld.

**Q11. What are the retention and session rules?**
Stored chats contain personal data. **Recommend:** an HTTP-only anonymous session cookie; chat
restored for 30 days, then deleted; operational logs carrying IDs and outcomes but never message
text or contact details. A 30-day window, or something else?

**Q12. How should this run, and on which port?**
Docker isn't installed on this machine, so the required `docker compose up` path can't be checked
here. Will you install it, or should I develop against the source-run platform and leave the Docker
path for you to verify? And should the webchat join `compose.yaml` or run separately? **Recommend:
install Docker; add the webchat to `compose.yaml` on port 4020**, without changing the two existing
services.
