from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import WRITE_TABLES, Database
from .errors import ApiError
from .validation import (
    optional_string,
    require_enum,
    require_integer,
    require_string,
    validate_contact,
    validate_phone,
    validate_registration,
)

UTC = timezone.utc
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
VEHICLE_ASSET_DIRECTORY = Path(__file__).resolve().parent.parent / "public" / "assets" / "vehicles"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def new_reference(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def camel_case(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {camel_case(key): row[key] for key in row.keys()}


class DealershipPlatform:
    def __init__(self, database: Database) -> None:
        self.database = database

    def _one(
        self,
        connection: sqlite3.Connection,
        query: str,
        parameters: tuple[Any, ...],
        *,
        label: str,
    ) -> sqlite3.Row:
        row = connection.execute(query, parameters).fetchone()
        if row is None:
            raise ApiError(404, "NOT_FOUND", f"{label} was not found.")
        return row

    def _require_idempotency_key(self, key: str | None) -> str:
        if not key or not key.strip():
            raise ApiError(
                400,
                "IDEMPOTENCY_KEY_REQUIRED",
                "Send a unique Idempotency-Key header for this operation.",
            )
        if len(key) > 100:
            raise ApiError(400, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key is too long.")
        return key.strip()

    def _replayed_resource(
        self,
        connection: sqlite3.Connection,
        resource: str,
        key: str | None,
        body: dict[str, Any],
        table: str,
    ) -> dict[str, Any] | None:
        if not key:
            return None
        existing = connection.execute(
            """
            SELECT request_hash, resource_id
            FROM idempotency_keys
            WHERE resource = ? AND idempotency_key = ?
            """,
            (resource, key),
        ).fetchone()
        if existing is None:
            return None
        if existing["request_hash"] != self.database.request_hash(body):
            raise ApiError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "This Idempotency-Key has already been used with a different request.",
            )
        record = self._one(
            connection,
            f'SELECT * FROM "{table}" WHERE id = ?',
            (existing["resource_id"],),
            label="Saved record",
        )
        response = row_to_dict(record) or {}
        response["idempotentReplay"] = True
        return response

    def _save_idempotency(
        self,
        connection: sqlite3.Connection,
        resource: str,
        key: str | None,
        body: dict[str, Any],
        resource_id: str,
        status: int = 201,
    ) -> None:
        if key:
            connection.execute(
                """
                INSERT INTO idempotency_keys
                (resource, idempotency_key, request_hash, resource_id, response_status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (resource, key, self.database.request_hash(body), resource_id, status),
            )

    def _validated_dealership(
        self, connection: sqlite3.Connection, dealership_id: str
    ) -> sqlite3.Row:
        return self._one(
            connection,
            "SELECT * FROM dealerships WHERE id = ?",
            (dealership_id,),
            label="Dealership",
        )

    def _validated_vehicle(
        self, connection: sqlite3.Connection, vehicle_id: str
    ) -> sqlite3.Row:
        return self._one(
            connection,
            "SELECT * FROM vehicles WHERE id = ?",
            (vehicle_id,),
            label="Vehicle",
        )

    def list_dealerships(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM dealerships ORDER BY name").fetchall()
        return {"items": [self._dealership_response(row) for row in rows]}

    def get_dealership(self, dealership_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._validated_dealership(connection, dealership_id)
        return self._dealership_response(row)

    def _dealership_response(self, row: sqlite3.Row) -> dict[str, Any]:
        response = row_to_dict(row) or {}
        response["brands"] = row["brands"].split(",")
        return response

    def get_opening_hours(self, dealership_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            self._validated_dealership(connection, dealership_id)
            weekly = connection.execute(
                """
                SELECT department, day_of_week, opens_at, closes_at
                FROM opening_hours
                WHERE dealership_id = ?
                ORDER BY department, day_of_week
                """,
                (dealership_id,),
            ).fetchall()
            holidays = connection.execute(
                """
                SELECT department, date, label, opens_at, closes_at
                FROM holiday_hours
                WHERE dealership_id = ?
                ORDER BY date, department
                """,
                (dealership_id,),
            ).fetchall()
        weekly_response = []
        for row in weekly:
            weekly_response.append(
                {
                    "department": row["department"],
                    "day": DAY_NAMES[row["day_of_week"]],
                    "dayOfWeek": row["day_of_week"],
                    "opensAt": row["opens_at"],
                    "closesAt": row["closes_at"],
                    "closed": row["opens_at"] is None,
                }
            )
        holiday_response = []
        for row in holidays:
            item = row_to_dict(row) or {}
            item["closed"] = row["opens_at"] is None
            holiday_response.append(item)
        return {
            "dealershipId": dealership_id,
            "weekly": weekly_response,
            "holidayExceptions": holiday_response,
        }

    def list_vehicles(self, query: dict[str, str]) -> dict[str, Any]:
        clauses = ["1 = 1"]
        parameters: list[Any] = []
        exact_filters = {
            "make": "make",
            "model": "model",
            "fuelType": "fuel_type",
            "transmission": "transmission",
            "bodyStyle": "body_style",
            "availability": "availability",
            "dealershipId": "dealership_id",
        }
        for parameter, column in exact_filters.items():
            if query.get(parameter):
                clauses.append(f"LOWER({column}) = LOWER(?)")
                parameters.append(query[parameter])
        if query.get("q"):
            clauses.append(
                "LOWER(make || ' ' || model || ' ' || variant || ' ' || colour) LIKE LOWER(?)"
            )
            parameters.append(f"%{query['q']}%")
        integer_filters = (
            ("minPricePence", "price_pence", ">="),
            ("maxPricePence", "price_pence", "<="),
            ("maxMileage", "mileage", "<="),
            ("minYear", "year", ">="),
        )
        for parameter, column, operator in integer_filters:
            if query.get(parameter):
                try:
                    value = int(query[parameter])
                except ValueError as error:
                    raise ApiError(
                        422,
                        "VALIDATION_ERROR",
                        f"{parameter} must be a whole number.",
                    ) from error
                clauses.append(f"{column} {operator} ?")
                parameters.append(value)
        try:
            page = max(1, int(query.get("page", "1")))
            page_size = min(50, max(1, int(query.get("pageSize", "12"))))
        except ValueError as error:
            raise ApiError(422, "VALIDATION_ERROR", "Pagination values must be numbers.") from error

        order_by = {
            "priceAsc": "price_pence IS NULL, price_pence ASC",
            "priceDesc": "price_pence IS NULL, price_pence DESC",
            "mileageAsc": "mileage ASC",
            "newest": "year DESC, updated_at DESC",
        }.get(query.get("sort", "newest"), "year DESC, updated_at DESC")
        where = " AND ".join(clauses)
        with self.database.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM vehicles WHERE {where}", tuple(parameters)
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT vehicles.*, dealerships.name AS dealership_name,
                       dealerships.town AS dealership_town
                FROM vehicles
                JOIN dealerships ON dealerships.id = vehicles.dealership_id
                WHERE {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                (*parameters, page_size, (page - 1) * page_size),
            ).fetchall()
        return {
            "items": [self._vehicle_response(row) for row in rows],
            "pagination": {
                "page": page,
                "pageSize": page_size,
                "totalItems": total,
                "totalPages": (total + page_size - 1) // page_size,
            },
        }

    def get_vehicle(self, vehicle_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._one(
                connection,
                """
                SELECT vehicles.*, dealerships.name AS dealership_name,
                       dealerships.town AS dealership_town
                FROM vehicles
                JOIN dealerships ON dealerships.id = vehicles.dealership_id
                WHERE vehicles.id = ?
                """,
                (vehicle_id,),
                label="Vehicle",
            )
        response = self._vehicle_response(row)
        response["highlights"] = [
            "Northstar 120-point inspection",
            "Minimum six-month warranty",
            "Nationwide delivery available",
        ]
        return response

    def _vehicle_response(self, row: sqlite3.Row) -> dict[str, Any]:
        response = row_to_dict(row) or {}
        vehicle_id = row["id"]
        response["images"] = self._vehicle_images(vehicle_id)
        response.pop("imageSlug", None)
        return response

    def _vehicle_images(self, vehicle_id: str) -> list[str]:
        if (VEHICLE_ASSET_DIRECTORY / f"{vehicle_id}.jpg").is_file():
            return [f"/assets/vehicles/{vehicle_id}.jpg"]
        return [
            f"/assets/vehicles/{vehicle_id}.svg",
            f"/assets/vehicles/{vehicle_id}.svg?view=side",
            f"/assets/vehicles/{vehicle_id}.svg?view=detail",
        ]

    def get_vehicle_availability(self, vehicle_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            vehicle = self._validated_vehicle(connection, vehicle_id)
            next_slot = connection.execute(
                """
                SELECT id, starts_at
                FROM test_drive_slots
                WHERE vehicle_id = ? AND status = 'available'
                ORDER BY starts_at
                LIMIT 1
                """,
                (vehicle_id,),
            ).fetchone()
        return {
            "vehicleId": vehicle_id,
            "availability": vehicle["availability"],
            "canEnquire": True,
            "canBookTestDrive": vehicle["availability"] == "available" and next_slot is not None,
            "canRegisterInterest": vehicle["availability"] == "reserved",
            "nextTestDriveSlot": row_to_dict(next_slot),
        }

    def list_offers(self, query: dict[str, str]) -> dict[str, Any]:
        clauses = ["1 = 1"]
        parameters: list[Any] = []
        if query.get("make"):
            clauses.append("LOWER(make) = LOWER(?)")
            parameters.append(query["make"])
        if query.get("productType"):
            clauses.append("LOWER(product_type) = LOWER(?)")
            parameters.append(query["productType"])
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM offers WHERE {' AND '.join(clauses)} ORDER BY monthly_price_pence",
                tuple(parameters),
            ).fetchall()
        return {"items": [self._offer_response(row) for row in rows]}

    def get_offer(self, offer_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._one(
                connection, "SELECT * FROM offers WHERE id = ?", (offer_id,), label="Offer"
            )
        return self._offer_response(row)

    def _offer_response(self, row: sqlite3.Row) -> dict[str, Any]:
        response = row_to_dict(row) or {}
        response["image"] = self._vehicle_images(row["image_slug"])[0]
        response.pop("imageSlug", None)
        return response

    def list_service_types(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM service_types ORDER BY name").fetchall()
        return {"items": [row_to_dict(row) for row in rows]}

    def list_test_drive_slots(self, query: dict[str, str]) -> dict[str, Any]:
        clauses = ["test_drive_slots.status = 'available'"]
        parameters: list[Any] = []
        for parameter, column in (
            ("dealershipId", "test_drive_slots.dealership_id"),
            ("vehicleId", "test_drive_slots.vehicle_id"),
        ):
            if query.get(parameter):
                clauses.append(f"{column} = ?")
                parameters.append(query[parameter])
        if query.get("dateFrom"):
            clauses.append("DATE(starts_at) >= DATE(?)")
            parameters.append(query["dateFrom"])
        if query.get("dateTo"):
            clauses.append("DATE(starts_at) <= DATE(?)")
            parameters.append(query["dateTo"])
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT test_drive_slots.*, dealerships.name AS dealership_name,
                       vehicles.make, vehicles.model, vehicles.variant
                FROM test_drive_slots
                JOIN dealerships ON dealerships.id = test_drive_slots.dealership_id
                JOIN vehicles ON vehicles.id = test_drive_slots.vehicle_id
                WHERE {' AND '.join(clauses)}
                ORDER BY starts_at
                LIMIT 100
                """,
                tuple(parameters),
            ).fetchall()
        return {"items": [row_to_dict(row) for row in rows]}

    def list_workshop_locations(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM dealerships ORDER BY name").fetchall()
        return {"items": [self._dealership_response(row) for row in rows]}

    def list_workshop_availability(self, query: dict[str, str]) -> dict[str, Any]:
        clauses = ["workshop_slots.status = 'available'"]
        parameters: list[Any] = []
        for parameter, column in (
            ("dealershipId", "workshop_slots.dealership_id"),
            ("serviceTypeId", "workshop_slots.service_type_id"),
        ):
            if query.get(parameter):
                clauses.append(f"{column} = ?")
                parameters.append(query[parameter])
        if query.get("dateFrom"):
            clauses.append("DATE(starts_at) >= DATE(?)")
            parameters.append(query["dateFrom"])
        if query.get("dateTo"):
            clauses.append("DATE(starts_at) <= DATE(?)")
            parameters.append(query["dateTo"])
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT workshop_slots.*, dealerships.name AS dealership_name,
                       service_types.name AS service_name,
                       service_types.duration_minutes,
                       service_types.price_from_pence
                FROM workshop_slots
                JOIN dealerships ON dealerships.id = workshop_slots.dealership_id
                JOIN service_types ON service_types.id = workshop_slots.service_type_id
                WHERE {' AND '.join(clauses)}
                ORDER BY starts_at
                LIMIT 100
                """,
                tuple(parameters),
            ).fetchall()
        return {"items": [row_to_dict(row) for row in rows]}

    def get_business_information(self) -> dict[str, Any]:
        return {
            "organisation": "Northstar Motors",
            "currency": "GBP",
            "market": "United Kingdom",
            "finance": {
                "notice": (
                    "Finance is subject to status, terms, and availability. "
                    "Northstar Motors acts as a credit broker, not a lender."
                ),
                "minimumAge": 18,
            },
            "partExchange": {
                "estimateNotice": (
                    "Online estimates are indicative and subject to physical inspection, "
                    "provenance checks, and market conditions."
                )
            },
            "privacyContact": "privacy@northstarmotors.example",
        }

    def create_sales_enquiry(
        self, body: dict[str, Any], idempotency_key: str | None
    ) -> dict[str, Any]:
        dealership_id = require_string(body, "dealershipId", maximum=80)
        vehicle_id = optional_string(body, "vehicleId", maximum=80)
        enquiry_type = require_enum(
            body, "enquiryType", {"general", "availability", "finance", "part-exchange"}
        )
        contact = validate_contact(body)
        message = require_string(body, "message", minimum=5, maximum=2_000)
        with self.database.connect() as connection:
            replay = self._replayed_resource(
                connection, "sales-enquiry", idempotency_key, body, "sales_enquiries"
            )
            if replay:
                return replay
            self._validated_dealership(connection, dealership_id)
            if vehicle_id:
                self._validated_vehicle(connection, vehicle_id)
            record_id = new_id("enq")
            reference = new_reference("SALE")
            created_at = utc_now()
            connection.execute(
                """
                INSERT INTO sales_enquiries
                (id, reference, dealership_id, vehicle_id, enquiry_type, first_name,
                 last_name, email, phone, message, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'received', ?)
                """,
                (
                    record_id,
                    reference,
                    dealership_id,
                    vehicle_id,
                    enquiry_type,
                    contact["firstName"],
                    contact["lastName"],
                    contact["email"],
                    contact["phone"],
                    message,
                    created_at,
                ),
            )
            self._save_idempotency(
                connection, "sales-enquiry", idempotency_key, body, record_id
            )
            row = connection.execute(
                "SELECT * FROM sales_enquiries WHERE id = ?", (record_id,)
            ).fetchone()
        return row_to_dict(row) or {}

    def create_test_drive_booking(
        self, body: dict[str, Any], idempotency_key: str | None
    ) -> dict[str, Any]:
        key = self._require_idempotency_key(idempotency_key)
        slot_id = require_string(body, "slotId", maximum=80)
        contact = validate_contact(body)
        notes = optional_string(body, "notes")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._replayed_resource(
                connection, "test-drive-booking", key, body, "test_drive_bookings"
            )
            if replay:
                return replay
            slot = self._one(
                connection,
                """
                SELECT test_drive_slots.*, vehicles.availability
                FROM test_drive_slots
                JOIN vehicles ON vehicles.id = test_drive_slots.vehicle_id
                WHERE test_drive_slots.id = ?
                """,
                (slot_id,),
                label="Test-drive slot",
            )
            if slot["availability"] == "reserved":
                raise ApiError(
                    409,
                    "VEHICLE_RESERVED",
                    "The vehicle is reserved and cannot be booked for a test drive.",
                )
            if slot["availability"] != "available":
                raise ApiError(
                    409, "VEHICLE_UNAVAILABLE", "The vehicle is not available for a test drive."
                )
            if slot["status"] != "available":
                raise ApiError(409, "SLOT_UNAVAILABLE", "That test-drive slot is unavailable.")
            record_id = new_id("tdb")
            reference = new_reference("TEST")
            created_at = utc_now()
            connection.execute(
                """
                INSERT INTO test_drive_bookings
                (id, reference, slot_id, dealership_id, vehicle_id, first_name,
                 last_name, email, phone, notes, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)
                """,
                (
                    record_id,
                    reference,
                    slot_id,
                    slot["dealership_id"],
                    slot["vehicle_id"],
                    contact["firstName"],
                    contact["lastName"],
                    contact["email"],
                    contact["phone"],
                    notes,
                    created_at,
                ),
            )
            connection.execute(
                "UPDATE test_drive_slots SET status = 'booked' WHERE id = ?", (slot_id,)
            )
            self._save_idempotency(
                connection, "test-drive-booking", key, body, record_id
            )
            row = connection.execute(
                "SELECT * FROM test_drive_bookings WHERE id = ?", (record_id,)
            ).fetchone()
        return row_to_dict(row) or {}

    def create_vehicle_interest(
        self, body: dict[str, Any], idempotency_key: str | None
    ) -> dict[str, Any]:
        vehicle_id = require_string(body, "vehicleId", maximum=80)
        contact = validate_contact(body)
        notes = optional_string(body, "notes")
        with self.database.connect() as connection:
            replay = self._replayed_resource(
                connection, "vehicle-interest", idempotency_key, body, "vehicle_interests"
            )
            if replay:
                return replay
            vehicle = self._validated_vehicle(connection, vehicle_id)
            if vehicle["availability"] != "reserved":
                raise ApiError(
                    409,
                    "VEHICLE_NOT_RESERVED",
                    "Interest can only be registered for a reserved vehicle.",
                )
            record_id = new_id("int")
            reference = new_reference("WAIT")
            created_at = utc_now()
            connection.execute(
                """
                INSERT INTO vehicle_interests
                (id, reference, dealership_id, vehicle_id, first_name, last_name,
                 email, phone, notes, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'registered', ?)
                """,
                (
                    record_id,
                    reference,
                    vehicle["dealership_id"],
                    vehicle_id,
                    contact["firstName"],
                    contact["lastName"],
                    contact["email"],
                    contact["phone"],
                    notes,
                    created_at,
                ),
            )
            self._save_idempotency(
                connection, "vehicle-interest", idempotency_key, body, record_id
            )
            row = connection.execute(
                "SELECT * FROM vehicle_interests WHERE id = ?", (record_id,)
            ).fetchone()
        return row_to_dict(row) or {}

    def create_callback_request(
        self, body: dict[str, Any], idempotency_key: str | None
    ) -> dict[str, Any]:
        dealership_id = require_string(body, "dealershipId", maximum=80)
        department = require_enum(body, "department", {"sales", "service", "parts"})
        vehicle_id = optional_string(body, "vehicleId", maximum=80)
        contact = validate_contact(body)
        preferred_time = optional_string(body, "preferredTime", maximum=120)
        reason = require_string(body, "reason", minimum=5, maximum=1_000)
        with self.database.connect() as connection:
            replay = self._replayed_resource(
                connection, "callback-request", idempotency_key, body, "callback_requests"
            )
            if replay:
                return replay
            self._validated_dealership(connection, dealership_id)
            if vehicle_id:
                self._validated_vehicle(connection, vehicle_id)
            record_id = new_id("call")
            reference = new_reference("CALL")
            created_at = utc_now()
            connection.execute(
                """
                INSERT INTO callback_requests
                (id, reference, dealership_id, department, vehicle_id, first_name,
                 last_name, email, phone, preferred_time, reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'requested', ?)
                """,
                (
                    record_id,
                    reference,
                    dealership_id,
                    department,
                    vehicle_id,
                    contact["firstName"],
                    contact["lastName"],
                    contact["email"],
                    contact["phone"],
                    preferred_time,
                    reason,
                    created_at,
                ),
            )
            self._save_idempotency(
                connection, "callback-request", idempotency_key, body, record_id
            )
            row = connection.execute(
                "SELECT * FROM callback_requests WHERE id = ?", (record_id,)
            ).fetchone()
        return row_to_dict(row) or {}

    def create_workshop_booking(
        self, body: dict[str, Any], idempotency_key: str | None
    ) -> dict[str, Any]:
        key = self._require_idempotency_key(idempotency_key)
        slot_id = require_string(body, "slotId", maximum=80)
        registration = validate_registration(body.get("registration"))
        mileage = require_integer(body, "mileage", maximum=1_000_000)
        contact = validate_contact(body)
        notes = optional_string(body, "notes")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._replayed_resource(
                connection, "workshop-booking", key, body, "workshop_bookings"
            )
            if replay:
                return replay
            slot = self._one(
                connection,
                "SELECT * FROM workshop_slots WHERE id = ?",
                (slot_id,),
                label="Workshop slot",
            )
            if slot["status"] != "available":
                raise ApiError(409, "SLOT_UNAVAILABLE", "That workshop slot is unavailable.")
            record_id = new_id("wsb")
            reference = new_reference("WORK")
            created_at = utc_now()
            connection.execute(
                """
                INSERT INTO workshop_bookings
                (id, reference, slot_id, dealership_id, service_type_id, registration,
                 mileage, first_name, last_name, email, phone, notes, status,
                 created_at, updated_at, cancelled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, NULL)
                """,
                (
                    record_id,
                    reference,
                    slot_id,
                    slot["dealership_id"],
                    slot["service_type_id"],
                    registration,
                    mileage,
                    contact["firstName"],
                    contact["lastName"],
                    contact["email"],
                    contact["phone"],
                    notes,
                    created_at,
                    created_at,
                ),
            )
            connection.execute(
                "UPDATE workshop_slots SET status = 'booked' WHERE id = ?", (slot_id,)
            )
            self._save_idempotency(connection, "workshop-booking", key, body, record_id)
            row = connection.execute(
                "SELECT * FROM workshop_bookings WHERE id = ?", (record_id,)
            ).fetchone()
        return row_to_dict(row) or {}

    def find_workshop_booking(self, body: dict[str, Any]) -> dict[str, Any]:
        reference = require_string(body, "reference", maximum=80).upper()
        last_name = require_string(body, "lastName", maximum=100)
        registration = validate_registration(body.get("registration"))
        phone = validate_phone(body.get("phone"))

        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT booking.*,
                       slot.starts_at AS starts_at,
                       dealership.name AS dealership_name,
                       dealership.address_line AS dealership_address_line,
                       dealership.town AS dealership_town,
                       dealership.postcode AS dealership_postcode,
                       service_type.name AS service_type_name
                FROM workshop_bookings AS booking
                JOIN workshop_slots AS slot ON slot.id = booking.slot_id
                JOIN dealerships AS dealership ON dealership.id = booking.dealership_id
                JOIN service_types AS service_type ON service_type.id = booking.service_type_id
                WHERE booking.reference = ?
                """,
                (reference,),
            ).fetchone()

        details_match = (
            row is not None
            and row["last_name"].casefold() == last_name.casefold()
            and row["registration"].replace(" ", "") == registration.replace(" ", "")
            and row["phone"] == phone
        )
        if not details_match:
            raise ApiError(
                404,
                "BOOKING_NOT_FOUND",
                "No workshop booking matched those details.",
            )
        return row_to_dict(row) or {}

    def update_workshop_booking(
        self, booking_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        allowed = {"slotId", "mileage", "notes"}
        if not body or not set(body).issubset(allowed):
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "Supply one or more of: slotId, mileage, notes.",
            )
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            booking = self._one(
                connection,
                "SELECT * FROM workshop_bookings WHERE id = ?",
                (booking_id,),
                label="Workshop booking",
            )
            if booking["status"] == "cancelled":
                raise ApiError(409, "BOOKING_CANCELLED", "A cancelled booking cannot be amended.")
            slot_id = booking["slot_id"]
            if "slotId" in body and body["slotId"] != slot_id:
                next_slot_id = require_string(body, "slotId", maximum=80)
                next_slot = self._one(
                    connection,
                    "SELECT * FROM workshop_slots WHERE id = ?",
                    (next_slot_id,),
                    label="Workshop slot",
                )
                if next_slot["status"] != "available":
                    raise ApiError(409, "SLOT_UNAVAILABLE", "That workshop slot is unavailable.")
                connection.execute(
                    "UPDATE workshop_slots SET status = 'available' WHERE id = ?", (slot_id,)
                )
                connection.execute(
                    "UPDATE workshop_slots SET status = 'booked' WHERE id = ?", (next_slot_id,)
                )
                slot_id = next_slot_id
                dealership_id = next_slot["dealership_id"]
                service_type_id = next_slot["service_type_id"]
            else:
                dealership_id = booking["dealership_id"]
                service_type_id = booking["service_type_id"]
            mileage = (
                require_integer(body, "mileage", maximum=1_000_000)
                if "mileage" in body
                else booking["mileage"]
            )
            notes = (
                optional_string(body, "notes")
                if "notes" in body
                else booking["notes"]
            )
            connection.execute(
                """
                UPDATE workshop_bookings
                SET slot_id = ?, dealership_id = ?, service_type_id = ?,
                    mileage = ?, notes = ?, status = 'confirmed', updated_at = ?
                WHERE id = ?
                """,
                (
                    slot_id,
                    dealership_id,
                    service_type_id,
                    mileage,
                    notes,
                    utc_now(),
                    booking_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM workshop_bookings WHERE id = ?", (booking_id,)
            ).fetchone()
        return row_to_dict(row) or {}

    def cancel_workshop_booking(self, booking_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            booking = self._one(
                connection,
                "SELECT * FROM workshop_bookings WHERE id = ?",
                (booking_id,),
                label="Workshop booking",
            )
            if booking["status"] == "cancelled":
                return row_to_dict(booking) or {}
            cancelled_at = utc_now()
            connection.execute(
                """
                UPDATE workshop_bookings
                SET status = 'cancelled', cancelled_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (cancelled_at, cancelled_at, booking_id),
            )
            connection.execute(
                "UPDATE workshop_slots SET status = 'available' WHERE id = ?",
                (booking["slot_id"],),
            )
            row = connection.execute(
                "SELECT * FROM workshop_bookings WHERE id = ?", (booking_id,)
            ).fetchone()
        return row_to_dict(row) or {}

    def create_dealership_message(
        self, body: dict[str, Any], idempotency_key: str | None
    ) -> dict[str, Any]:
        dealership_id = require_string(body, "dealershipId", maximum=80)
        department = require_enum(body, "department", {"sales", "service", "parts", "general"})
        subject = require_string(body, "subject", minimum=3, maximum=120)
        message = require_string(body, "message", minimum=5, maximum=2_000)
        preferred_contact_method = require_enum(
            body, "preferredContactMethod", {"email", "phone"}
        )
        contact = validate_contact(body)
        with self.database.connect() as connection:
            replay = self._replayed_resource(
                connection, "dealership-message", idempotency_key, body, "dealership_messages"
            )
            if replay:
                return replay
            self._validated_dealership(connection, dealership_id)
            record_id = new_id("msg")
            reference = new_reference("MSG")
            created_at = utc_now()
            connection.execute(
                """
                INSERT INTO dealership_messages
                (id, reference, dealership_id, department, subject, message, first_name,
                 last_name, email, phone, preferred_contact_method, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'received', ?)
                """,
                (
                    record_id,
                    reference,
                    dealership_id,
                    department,
                    subject,
                    message,
                    contact["firstName"],
                    contact["lastName"],
                    contact["email"],
                    contact["phone"],
                    preferred_contact_method,
                    created_at,
                ),
            )
            self._save_idempotency(
                connection, "dealership-message", idempotency_key, body, record_id
            )
            row = connection.execute(
                "SELECT * FROM dealership_messages WHERE id = ?", (record_id,)
            ).fetchone()
        return row_to_dict(row) or {}

    def create_part_exchange_valuation(
        self, body: dict[str, Any], idempotency_key: str | None
    ) -> dict[str, Any]:
        dealership_id = require_string(body, "dealershipId", maximum=80)
        registration = validate_registration(body.get("registration"))
        mileage = require_integer(body, "mileage", maximum=1_000_000)
        condition = require_enum(body, "condition", {"excellent", "good", "fair"})
        contact = validate_contact(body)
        with self.database.connect() as connection:
            replay = self._replayed_resource(
                connection,
                "part-exchange-valuation",
                idempotency_key,
                body,
                "part_exchange_valuations",
            )
            if replay:
                return replay
            self._validated_dealership(connection, dealership_id)
            registration_factor = sum(ord(character) for character in registration) % 650_000
            condition_factor = {"excellent": 1.0, "good": 0.88, "fair": 0.72}[condition]
            base = max(175_000, 1_900_000 + registration_factor - mileage * 8)
            estimate_low = round(base * condition_factor / 5_000) * 5_000
            estimate_high = estimate_low + max(90_000, round(estimate_low * 0.12 / 5_000) * 5_000)
            record_id = new_id("px")
            reference = new_reference("PX")
            created_at = utc_now()
            connection.execute(
                """
                INSERT INTO part_exchange_valuations
                (id, reference, dealership_id, registration, mileage, condition,
                 first_name, last_name, email, phone, estimate_low_pence,
                 estimate_high_pence, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'estimated', ?)
                """,
                (
                    record_id,
                    reference,
                    dealership_id,
                    registration,
                    mileage,
                    condition,
                    contact["firstName"],
                    contact["lastName"],
                    contact["email"],
                    contact["phone"],
                    estimate_low,
                    estimate_high,
                    created_at,
                ),
            )
            self._save_idempotency(
                connection, "part-exchange-valuation", idempotency_key, body, record_id
            )
            row = connection.execute(
                "SELECT * FROM part_exchange_valuations WHERE id = ?", (record_id,)
            ).fetchone()
        response = row_to_dict(row) or {}
        response["estimateNotice"] = self.get_business_information()["partExchange"][
            "estimateNotice"
        ]
        return response

    def get_record(self, resource: str, record_id: str) -> dict[str, Any]:
        table = {
            "sales-enquiries": "sales_enquiries",
            "test-drive-bookings": "test_drive_bookings",
            "vehicle-interests": "vehicle_interests",
            "callback-requests": "callback_requests",
            "workshop-bookings": "workshop_bookings",
            "dealership-messages": "dealership_messages",
            "part-exchange-valuations": "part_exchange_valuations",
        }.get(resource)
        if not table:
            raise ApiError(404, "NOT_FOUND", "Resource was not found.")
        with self.database.connect() as connection:
            row = self._one(
                connection,
                f'SELECT * FROM "{table}" WHERE id = ?',
                (record_id,),
                label="Record",
            )
        return row_to_dict(row) or {}

    def admin_summary(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            counts = {
                camel_case(table): connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in WRITE_TABLES
            }
            recent_records: dict[str, list[dict[str, Any] | None]] = {}
            for table in WRITE_TABLES:
                rows = connection.execute(
                    f'SELECT * FROM "{table}" ORDER BY created_at DESC LIMIT 50'
                ).fetchall()
                recent_records[camel_case(table)] = [row_to_dict(row) for row in rows]
            request_rows = connection.execute(
                "SELECT * FROM request_log ORDER BY id DESC LIMIT 100"
            ).fetchall()
        return {
            "counts": counts,
            "records": recent_records,
            "requestLog": [row_to_dict(row) for row in request_rows],
        }
