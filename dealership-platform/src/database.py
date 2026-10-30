from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dealerships (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    town TEXT NOT NULL,
    postcode TEXT NOT NULL,
    address_line TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    brands TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opening_hours (
    dealership_id TEXT NOT NULL REFERENCES dealerships(id),
    department TEXT NOT NULL,
    day_of_week INTEGER NOT NULL,
    opens_at TEXT,
    closes_at TEXT,
    PRIMARY KEY (dealership_id, department, day_of_week)
);

CREATE TABLE IF NOT EXISTS holiday_hours (
    dealership_id TEXT NOT NULL REFERENCES dealerships(id),
    department TEXT NOT NULL,
    date TEXT NOT NULL,
    label TEXT NOT NULL,
    opens_at TEXT,
    closes_at TEXT,
    PRIMARY KEY (dealership_id, department, date)
);

CREATE TABLE IF NOT EXISTS vehicles (
    id TEXT PRIMARY KEY,
    dealership_id TEXT NOT NULL REFERENCES dealerships(id),
    make TEXT NOT NULL,
    model TEXT NOT NULL,
    variant TEXT NOT NULL,
    year INTEGER NOT NULL,
    price_pence INTEGER,
    monthly_price_pence INTEGER,
    mileage INTEGER NOT NULL,
    fuel_type TEXT NOT NULL,
    transmission TEXT NOT NULL,
    colour TEXT NOT NULL,
    body_style TEXT NOT NULL,
    availability TEXT NOT NULL,
    registration TEXT NOT NULL,
    description TEXT NOT NULL,
    image_slug TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS vehicles_search
ON vehicles (availability, make, model, price_pence, dealership_id);

CREATE TABLE IF NOT EXISTS offers (
    id TEXT PRIMARY KEY,
    make TEXT NOT NULL,
    model TEXT NOT NULL,
    title TEXT NOT NULL,
    product_type TEXT NOT NULL,
    monthly_price_pence INTEGER NOT NULL,
    upfront_payment_pence INTEGER NOT NULL,
    apr REAL,
    term_months INTEGER NOT NULL,
    annual_mileage INTEGER NOT NULL,
    expires_on TEXT NOT NULL,
    description TEXT NOT NULL,
    image_slug TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_types (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    price_from_pence INTEGER
);

CREATE TABLE IF NOT EXISTS test_drive_slots (
    id TEXT PRIMARY KEY,
    dealership_id TEXT NOT NULL REFERENCES dealerships(id),
    vehicle_id TEXT NOT NULL REFERENCES vehicles(id),
    starts_at TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workshop_slots (
    id TEXT PRIMARY KEY,
    dealership_id TEXT NOT NULL REFERENCES dealerships(id),
    service_type_id TEXT NOT NULL REFERENCES service_types(id),
    starts_at TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_enquiries (
    id TEXT PRIMARY KEY,
    reference TEXT UNIQUE NOT NULL,
    dealership_id TEXT NOT NULL REFERENCES dealerships(id),
    vehicle_id TEXT REFERENCES vehicles(id),
    enquiry_type TEXT NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_drive_bookings (
    id TEXT PRIMARY KEY,
    reference TEXT UNIQUE NOT NULL,
    slot_id TEXT UNIQUE NOT NULL REFERENCES test_drive_slots(id),
    dealership_id TEXT NOT NULL REFERENCES dealerships(id),
    vehicle_id TEXT NOT NULL REFERENCES vehicles(id),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    notes TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vehicle_interests (
    id TEXT PRIMARY KEY,
    reference TEXT UNIQUE NOT NULL,
    dealership_id TEXT NOT NULL REFERENCES dealerships(id),
    vehicle_id TEXT NOT NULL REFERENCES vehicles(id),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    notes TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS callback_requests (
    id TEXT PRIMARY KEY,
    reference TEXT UNIQUE NOT NULL,
    dealership_id TEXT NOT NULL REFERENCES dealerships(id),
    department TEXT NOT NULL,
    vehicle_id TEXT REFERENCES vehicles(id),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    preferred_time TEXT,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workshop_bookings (
    id TEXT PRIMARY KEY,
    reference TEXT UNIQUE NOT NULL,
    slot_id TEXT UNIQUE NOT NULL REFERENCES workshop_slots(id),
    dealership_id TEXT NOT NULL REFERENCES dealerships(id),
    service_type_id TEXT NOT NULL REFERENCES service_types(id),
    registration TEXT NOT NULL,
    mileage INTEGER NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    notes TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cancelled_at TEXT
);

CREATE TABLE IF NOT EXISTS dealership_messages (
    id TEXT PRIMARY KEY,
    reference TEXT UNIQUE NOT NULL,
    dealership_id TEXT NOT NULL REFERENCES dealerships(id),
    department TEXT NOT NULL,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    preferred_contact_method TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS part_exchange_valuations (
    id TEXT PRIMARY KEY,
    reference TEXT UNIQUE NOT NULL,
    dealership_id TEXT NOT NULL REFERENCES dealerships(id),
    registration TEXT NOT NULL,
    mileage INTEGER NOT NULL,
    condition TEXT NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    estimate_low_pence INTEGER NOT NULL,
    estimate_high_pence INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    resource TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    response_status INTEGER NOT NULL,
    PRIMARY KEY (resource, idempotency_key)
);

CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    response_status INTEGER NOT NULL,
    body_summary TEXT,
    occurred_at TEXT NOT NULL
);
"""

WRITE_TABLES = (
    "sales_enquiries",
    "test_drive_bookings",
    "vehicle_interests",
    "callback_requests",
    "workshop_bookings",
    "dealership_messages",
    "part_exchange_valuations",
)


class Database:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            count = connection.execute("SELECT COUNT(*) FROM dealerships").fetchone()[0]
            if count == 0:
                from .seed import seed_database

                seed_database(connection)

    def reset(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            tables = [
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                connection.execute(f'DELETE FROM "{table}"')
            connection.execute("DELETE FROM sqlite_sequence")
            connection.execute("PRAGMA foreign_keys = ON")
            from .seed import seed_database

            seed_database(connection)

    def log_request(
        self,
        method: str,
        path: str,
        response_status: int,
        body: dict[str, Any] | None,
        occurred_at: str,
    ) -> None:
        summary = None
        if body:
            personal_fields = {
                "email",
                "firstName",
                "lastName",
                "phone",
                "reference",
                "registration",
            }
            safe_body = {
                key: ("[redacted]" if key in personal_fields else value)
                for key, value in body.items()
            }
            summary = json.dumps(safe_body, separators=(",", ":"))[:1_000]
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO request_log (method, path, response_status, body_summary, occurred_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (method, path, response_status, summary, occurred_at),
            )

    @staticmethod
    def request_hash(body: dict[str, Any]) -> str:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
