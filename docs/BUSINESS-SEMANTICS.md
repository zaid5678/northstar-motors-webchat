# Dealership platform business semantics

This document defines what each platform response means. The OpenAPI contract defines request
and response shape.

## General rules

- All money values are integer pence in GBP.
- All timestamps are UTC ISO 8601 values.
- A write response includes a stable `id`, customer-facing `reference`, `status`, and
  `createdAt`.
- A successful HTTP response confirms only the status stated in its body.
- Structured errors include `code`, `message`, `fieldErrors`, and `retryable`.
- Public catalogue and reference reads do not require authentication. Saved customer-record
  reads and all writes require `X-API-Key`.
- An `Idempotency-Key` is mandatory when claiming a test-drive or workshop slot and recommended
  for every other write.
- Repeating the same operation with the same key and body returns the original record with
  `idempotentReplay: true`.
- Reusing a key with a different body returns `IDEMPOTENCY_CONFLICT`.

## Vehicle availability

`available`

- Sales enquiries are accepted.
- A test drive can be booked when an available slot exists for the vehicle.
- Registering interest is rejected because the vehicle is not reserved.

`reserved`

- Sales enquiries are accepted.
- Test-drive booking is rejected with `VEHICLE_RESERVED`.
- Interest can be registered with status `registered`.

`sold`

- A sales enquiry can still be accepted.
- Test-drive booking is rejected with `VEHICLE_UNAVAILABLE`.
- Registering interest is rejected because the vehicle is not reserved.

Availability may change between a search and an operation.

## Sales operations

### Sales enquiry

An accepted enquiry has status `received`. This means the details were saved for the dealership;
it does not promise a response time, finance approval, or vehicle availability.

### Test-drive booking

A created booking has status `confirmed`. A booking is confirmed only when both the vehicle and
slot remain available.

If another request claims the slot first, the platform returns `SLOT_UNAVAILABLE`. No booking is
created.

### Vehicle interest

A created record has status `registered`. It records interest in a currently reserved vehicle.
It does not create a queue position or guarantee that the vehicle will become available.

### Callback request

A created callback has status `requested`. This means the dealership received the request; it
does not guarantee an exact callback time.

### Part-exchange valuation

A created valuation has status `estimated` and an `estimateLowPence` to `estimateHighPence`
range. The estimate is indicative and remains subject to inspection, provenance checks, and
market conditions.

## Workshop operations

### Workshop booking

A created booking has status `confirmed` and the selected slot becomes unavailable.

An amendment returns the complete updated booking. A successful move releases the old slot and
reserves the new one. If the new slot has already been claimed, the platform returns
`SLOT_UNAVAILABLE` and the booking remains unchanged.

A cancellation changes the booking status to `cancelled`, records `cancelledAt`, and releases
the slot. Repeating the cancellation returns the already-cancelled booking.

An existing booking can be retrieved by matching its reference, customer surname, vehicle
registration, and phone number. All four values must match. A successful lookup includes the
appointment time, service name, and dealership details. A mismatch returns `BOOKING_NOT_FOUND`
without identifying which value was incorrect.

The Bolton workshop has no available slots during the first seven seeded days. This is valid
business availability, not a platform failure.

## Messages

A saved dealership message has status `received`. It confirms that the chosen department can
inspect the message; it does not confirm that a reply has been sent.

## Error handling

Common error codes:

| Code                   | Meaning                                                |
| ---------------------- | ------------------------------------------------------ |
| `VALIDATION_ERROR`     | One or more supplied fields are invalid                |
| `NOT_FOUND`            | The resource or endpoint does not exist                |
| `BOOKING_NOT_FOUND`    | No workshop booking matched the supplied identity data |
| `SLOT_UNAVAILABLE`     | A slot is blocked or has already been claimed          |
| `VEHICLE_RESERVED`     | A reserved vehicle cannot be booked for a test drive   |
| `VEHICLE_UNAVAILABLE`  | The vehicle cannot be used for the requested operation |
| `IDEMPOTENCY_CONFLICT` | A key was reused for a different request               |
| `UNAUTHORISED`         | The required local key is missing or incorrect         |
| `INTERNAL_ERROR`       | The local platform could not complete the request      |
