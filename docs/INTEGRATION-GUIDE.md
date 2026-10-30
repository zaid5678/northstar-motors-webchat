# Dealership platform integration guide

## Base URL and contract

The local base URL is:

```text
http://localhost:4010
```

The machine-readable contract is available at:

```text
http://localhost:4010/openapi.json
```

Interactive documentation is available at http://localhost:4010/docs.

## Authentication

Inventory, offer, location, availability, service-type, and business-information reads are
public. Reading a saved customer record and every write operation require:

```http
X-API-Key: northstar-local-development
```

Keep this value in server-side configuration. Do not expose it in browser-delivered code.

## Safe retries

Supply a caller-generated `Idempotency-Key` when creating a record. It is required for
test-drive and workshop bookings.

```http
Idempotency-Key: 2bb548cc-2905-44c8-b190-ae554eead913
```

If a request times out, retry the same body with the same key. For a genuinely new operation,
generate a new key.

## Typical vehicle flow

Search inventory:

```bash
curl "http://localhost:4010/api/vehicles?bodyStyle=SUV&fuelType=Hybrid&maxPricePence=4500000"
```

Check a vehicle's current business state:

```bash
curl "http://localhost:4010/api/vehicles/veh-001/availability"
```

Find a test-drive slot for that vehicle:

```bash
curl "http://localhost:4010/api/test-drive-slots?vehicleId=veh-001"
```

Create a booking using a returned slot ID:

```bash
curl -X POST "http://localhost:4010/api/test-drive-bookings" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: northstar-local-development" \
  -H "Idempotency-Key: test-drive-2bb548cc" \
  -d '{
    "slotId": "replace-with-a-returned-slot-id",
    "firstName": "Jamie",
    "lastName": "Taylor",
    "email": "jamie@example.com",
    "phone": "07700900123",
    "notes": "Automatic transmission preferred"
  }'
```

## Typical workshop flow

1. Read `/api/service-types`.
2. Read `/api/workshop-locations`.
3. Query `/api/workshop-availability` with a dealership and service type.
4. Create `/api/workshop-bookings` using a returned slot ID and an idempotency key.
5. Store the returned booking ID and reference.
6. Use `PATCH /api/workshop-bookings/{id}` to amend it.
7. Use `DELETE /api/workshop-bookings/{id}` to cancel it.

### Existing workshop booking

An existing workshop booking can be retrieved using its customer-facing reference together with
the customer's surname, vehicle registration, and phone number:

```bash
curl -X POST "http://localhost:4010/api/workshop-bookings/lookup" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: northstar-local-development" \
  -d '{
    "reference": "WORK-10001",
    "lastName": "Taylor",
    "registration": "AB12 CDE",
    "phone": "07700900123"
  }'
```

All four values must match the booking. The returned record includes the appointment time,
service, dealership, status, and internal booking ID.

## Errors

Every expected error has this shape:

```json
{
  "error": {
    "code": "SLOT_UNAVAILABLE",
    "message": "That workshop slot is unavailable.",
    "fieldErrors": {},
    "retryable": false
  }
}
```

Use `code` for program behaviour and `message` as a useful fallback. A validation response may
contain one or more field-specific explanations in `fieldErrors`.

## Images

Vehicle and offer responses include image paths such as:

```text
/assets/vehicles/veh-001.jpg
```

Resolve relative asset paths against the platform base URL.

Vehicle pages use stable links:

```text
http://localhost:4173/?vehicle=veh-001
```

Use the vehicle ID returned by the inventory API.

## Local observability and reset

The Systems Console at http://localhost:4010/admin displays saved business records and the most
recent API requests. The request log redacts customer identity and contact details.

Reset dealership-platform records and restore the seed data with:

```bash
./reset.sh
```
