# Northstar Motors AI Webchat

This repository contains the Northstar Motors website, local dealership services, and the
requirements in [PRODUCT-BRIEF.md](./PRODUCT-BRIEF.md).

## Start

Requirements:

- Docker with Docker Compose
- Ports `4010` and `4173` available

```bash
docker compose up --build -d
```

Then open:

- Dealership website: http://localhost:4173
- API documentation: http://localhost:4010/docs
- Dealership Systems Console: http://localhost:4010/admin

Public catalogue and reference reads do not require authentication. Saved customer-record reads
and write operations require this local API key:

```text
northstar-local-development
```

Send it using the `X-API-Key` header. Keep the key in server-side code rather than
browser-delivered code.

Start with:

- [PRODUCT-BRIEF.md](./PRODUCT-BRIEF.md) for the product requirements;
- [docs/INTEGRATION-GUIDE.md](./docs/INTEGRATION-GUIDE.md) for API usage;
- [docs/BUSINESS-SEMANTICS.md](./docs/BUSINESS-SEMANTICS.md) for operation outcomes;
- [docs/SEEDED-SCENARIOS.md](./docs/SEEDED-SCENARIOS.md) for the seed data catalogue.

## Reset

```bash
./reset.sh
```

Resetting clears dealership-platform records and restores the original seed data.

## Stop

```bash
docker compose down
```

State is retained in the `northstar-platform-data` Docker volume until reset or removal.

## Dealership services

The dealership platform provides:

- vehicle inventory and offers;
- dealership locations and opening hours;
- sales enquiries, callbacks, and test drives;
- workshop availability and bookings;
- dealership messages and part-exchange valuations.

The customer website is editable and uses the same vehicle inventory API.
