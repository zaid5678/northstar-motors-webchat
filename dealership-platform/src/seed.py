from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone

UTC = timezone.utc

DEALERSHIPS = [
    (
        "northstar-manchester",
        "Northstar Manchester",
        "Manchester",
        "M20 2YY",
        "101 Kingsway",
        "0161 555 0101",
        "manchester@northstarmotors.example",
        53.424,
        -2.231,
        "BMW,MINI,Volvo",
    ),
    (
        "northstar-stockport",
        "Northstar Stockport",
        "Stockport",
        "SK4 2BE",
        "24 Wellington Road",
        "0161 555 0124",
        "stockport@northstarmotors.example",
        53.419,
        -2.167,
        "BMW,MINI,Kia",
    ),
    (
        "northstar-liverpool",
        "Northstar Liverpool",
        "Liverpool",
        "L5 9YN",
        "8 Regent Road",
        "0151 555 0188",
        "liverpool@northstarmotors.example",
        53.425,
        -2.998,
        "Jaguar,Land Rover,Volvo",
    ),
    (
        "northstar-bolton",
        "Northstar Bolton",
        "Bolton",
        "BL3 2AW",
        "77 Manchester Road",
        "01204 555 204",
        "bolton@northstarmotors.example",
        53.572,
        -2.429,
        "BMW,Kia,Land Rover",
    ),
]

MODEL_CATALOGUE = [
    ("BMW", "1 Series", "118i M Sport", "Hatchback", "Petrol", "Automatic"),
    ("BMW", "3 Series", "320d M Sport", "Saloon", "Diesel", "Automatic"),
    ("BMW", "X3", "xDrive20d M Sport", "SUV", "Diesel", "Automatic"),
    ("BMW", "i4", "eDrive40 M Sport", "Hatchback", "Electric", "Automatic"),
    ("MINI", "Cooper", "Cooper S Exclusive", "Hatchback", "Petrol", "Automatic"),
    ("MINI", "Countryman", "Cooper Classic", "SUV", "Petrol", "Automatic"),
    ("Jaguar", "F-PACE", "R-Dynamic S", "SUV", "Diesel", "Automatic"),
    ("Land Rover", "Range Rover Evoque", "Dynamic SE", "SUV", "Hybrid", "Automatic"),
    ("Land Rover", "Discovery Sport", "Dynamic S", "SUV", "Diesel", "Automatic"),
    ("Volvo", "XC40", "Plus", "SUV", "Electric", "Automatic"),
    ("Volvo", "V60", "Core", "Estate", "Hybrid", "Automatic"),
    ("Kia", "Sportage", "GT-Line", "SUV", "Hybrid", "Automatic"),
]

COLOURS = ["Alpine White", "Midnight Black", "Atlantic Blue", "Silver Grey", "Forest Green"]

VEHICLE_COLOURS = {
    "veh-001": "Thundernight Purple",
    "veh-002": "Tanzanite Blue",
    "veh-003": "Tanzanite Blue",
    "veh-004": "Portimao Blue",
    "veh-005": "British Racing Green",
    "veh-006": "Blazing Blue",
    "veh-007": "Corris Grey",
    "veh-008": "Bronze",
    "veh-009": "Fuji White",
    "veh-010": "Crystal White",
    "veh-011": "Osmium Grey",
    "veh-012": "Orange Fusion",
    "veh-013": "Fire Red",
    "veh-014": "Brooklyn Grey",
    "veh-015": "Black Sapphire",
    "veh-016": "Brooklyn Grey",
    "veh-017": "Legend Grey",
    "veh-018": "Smokey Green",
    "veh-019": "British Racing Green",
    "veh-020": "Seoul Pearl Silver",
    "veh-021": "Fuji White",
    "veh-022": "Bright Silver",
    "veh-023": "Pebble Grey",
    "veh-024": "White Pearl",
    "veh-025": "Storm Bay",
    "veh-026": "Arctic Race Blue",
    "veh-027": "Fire Red",
    "veh-028": "Alpine White",
    "veh-029": "Midnight Black",
    "veh-030": "Legend Grey",
    "veh-031": "Eiger Grey",
    "veh-032": "Fuji White",
    "veh-033": "Firenze Red",
    "veh-034": "Black Stone",
    "veh-035": "Fusion Red",
    "veh-036": "Gravity Grey",
    "veh-037": "Skyscraper Grey",
    "veh-038": "Dravit Grey",
    "veh-039": "Dune Grey",
    "veh-040": "Black Sapphire",
    "veh-041": "Midnight Black",
    "veh-042": "Midnight Black with Chili Red roof",
    "veh-043": "Ultra Blue",
    "veh-044": "Santorini Black",
    "veh-045": "Firenze Red",
    "veh-046": "Bright Silver",
    "veh-047": "Bursting Blue",
    "veh-048": "Jungle Green",
    "veh-049": "Fire Red",
    "veh-050": "Tanzanite Blue",
    "veh-051": "Black Sapphire",
    "veh-052": "Alpine White",
    "veh-053": "Legend Grey",
    "veh-054": "Midnight Black",
    "veh-055": "Carpathian Grey",
    "veh-056": "Silver",
    "veh-057": "Portofino Blue",
    "veh-058": "Fusion Red",
    "veh-059": "Onyx Black",
    "veh-060": "White Pearl",
}

SERVICE_TYPES = [
    ("interim-service", "Interim service", "Routine oil and safety inspection.", 90, 18900),
    ("full-service", "Full service", "Comprehensive annual vehicle service.", 180, 32900),
    ("mot", "MOT", "Annual MOT inspection.", 60, 5499),
    ("service-and-mot", "Service and MOT", "Combined full service and MOT.", 240, 36900),
    ("diagnostic", "Diagnostic inspection", "Investigation of a warning light or fault.", 90, 12900),
    ("brake-inspection", "Brake inspection", "Brake condition and performance inspection.", 60, 7900),
    ("tyre-fitting", "Tyre fitting", "Tyre replacement and balancing.", 90, None),
    ("recall", "Manufacturer recall", "Manufacturer recall or quality enhancement.", 120, 0),
]


def iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def seed_database(connection: sqlite3.Connection) -> None:
    connection.executemany(
        """
        INSERT INTO dealerships
        (id, name, town, postcode, address_line, phone, email, latitude, longitude, brands)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        DEALERSHIPS,
    )

    for dealership in DEALERSHIPS:
        dealership_id = dealership[0]
        for department in ("sales", "service", "parts"):
            for day in range(7):
                if day == 6:
                    opens_at = None
                    closes_at = None
                elif day == 5:
                    opens_at = "09:00"
                    closes_at = "17:00" if department == "sales" else "13:00"
                else:
                    opens_at = "08:30" if department != "sales" else "09:00"
                    closes_at = "18:00" if department == "sales" else "17:30"
                connection.execute(
                    """
                    INSERT INTO opening_hours
                    (dealership_id, department, day_of_week, opens_at, closes_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (dealership_id, department, day, opens_at, closes_at),
                )

    next_bank_holiday = date.today() + timedelta(days=30)
    for dealership in DEALERSHIPS:
        connection.execute(
            """
            INSERT INTO holiday_hours
            (dealership_id, department, date, label, opens_at, closes_at)
            VALUES (?, 'sales', ?, 'Bank holiday', '10:00', '16:00')
            """,
            (dealership[0], next_bank_holiday.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO holiday_hours
            (dealership_id, department, date, label, opens_at, closes_at)
            VALUES (?, 'service', ?, 'Bank holiday', NULL, NULL)
            """,
            (dealership[0], next_bank_holiday.isoformat()),
        )

    timestamp = iso_now()
    for index in range(60):
        vehicle_number = index + 1
        vehicle_id = f"veh-{vehicle_number:03d}"
        make, model, variant, body_style, fuel_type, transmission = MODEL_CATALOGUE[
            index % len(MODEL_CATALOGUE)
        ]
        dealership_id = DEALERSHIPS[index % len(DEALERSHIPS)][0]
        availability = (
            "reserved"
            if vehicle_number in {7, 28, 54}
            else "sold"
            if vehicle_number in {13, 37}
            else "available"
        )
        year = 2020 + (index % 7)
        price_pence = None if vehicle_number in {19, 46} else 1_850_000 + (index % 12) * 275_000
        monthly_price_pence = None if price_pence is None else round(price_pence * 0.0148)
        mileage = 3_500 + (index % 8) * 6_250
        colour = VEHICLE_COLOURS.get(vehicle_id, COLOURS[index % len(COLOURS)])
        description = (
            f"A carefully selected {year} {make} {model} in {colour.lower()}, "
            f"prepared by Northstar Motors with a full vehicle inspection."
        )
        connection.execute(
            """
            INSERT INTO vehicles
            (id, dealership_id, make, model, variant, year, price_pence,
             monthly_price_pence, mileage, fuel_type, transmission, colour,
             body_style, availability, registration, description, image_slug, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vehicle_id,
                dealership_id,
                make,
                model,
                variant,
                year,
                price_pence,
                monthly_price_pence,
                mileage,
                fuel_type,
                transmission,
                colour,
                body_style,
                availability,
                f"NS{vehicle_number:02d} CAR",
                description,
                vehicle_id,
                timestamp,
            ),
        )

    for index, model in enumerate(MODEL_CATALOGUE[:8], start=1):
        make, model_name, variant, *_ = model
        connection.execute(
            """
            INSERT INTO offers
            (id, make, model, title, product_type, monthly_price_pence,
             upfront_payment_pence, apr, term_months, annual_mileage,
             expires_on, description, image_slug)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"offer-{index:02d}",
                make,
                model_name,
                f"{make} {model_name} {variant}",
                "PCP" if index % 2 else "PCH",
                32900 + index * 3500,
                299900 + index * 25000,
                5.9 if index % 2 else None,
                48,
                8_000,
                (date.today() + timedelta(days=90)).isoformat(),
                "Published Northstar Motors new-car offer. Subject to status and availability.",
                f"veh-{index:03d}",
            ),
        )

    connection.executemany(
        """
        INSERT INTO service_types
        (id, name, description, duration_minutes, price_from_pence)
        VALUES (?, ?, ?, ?, ?)
        """,
        SERVICE_TYPES,
    )

    seed_slots(connection)
    seed_workshop_bookings(connection)


def seed_slots(connection: sqlite3.Connection) -> None:
    start_date = date.today() + timedelta(days=1)
    slot_number = 1
    for day_offset in range(21):
        slot_date = start_date + timedelta(days=day_offset)
        if slot_date.weekday() == 6:
            continue
        for dealership_index, dealership in enumerate(DEALERSHIPS):
            dealership_id = dealership[0]
            for hour in (9, 11, 14, 16):
                starts_at = datetime.combine(
                    slot_date,
                    time(hour=hour, minute=0),
                    tzinfo=UTC,
                ).isoformat().replace("+00:00", "Z")
                vehicle_number = dealership_index + 1 + 4 * ((slot_number // 4) % 15)
                vehicle_id = f"veh-{vehicle_number:03d}"
                connection.execute(
                    """
                    INSERT INTO test_drive_slots
                    (id, dealership_id, vehicle_id, starts_at, status)
                    VALUES (?, ?, ?, ?, 'available')
                    """,
                    (
                        f"td-slot-{slot_number:04d}",
                        dealership_id,
                        vehicle_id,
                        starts_at,
                    ),
                )
                service_type = SERVICE_TYPES[(slot_number + dealership_index) % len(SERVICE_TYPES)][0]
                workshop_status = (
                    "blocked"
                    if dealership_id == "northstar-bolton" and day_offset < 7
                    else "available"
                )
                connection.execute(
                    """
                    INSERT INTO workshop_slots
                    (id, dealership_id, service_type_id, starts_at, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        f"ws-slot-{slot_number:04d}",
                        dealership_id,
                        service_type,
                        starts_at,
                        workshop_status,
                    ),
                )
                slot_number += 1


def seed_workshop_bookings(connection: sqlite3.Connection) -> None:
    bookings = (
        (
            "wsb-seeded-001",
            "WORK-10001",
            "ws-slot-0001",
            "AB12 CDE",
            42150,
            "Jamie",
            "Taylor",
            "jamie.taylor@example.com",
            "07700900123",
            "Customer will wait at the dealership.",
        ),
        (
            "wsb-seeded-002",
            "WORK-10002",
            "ws-slot-0005",
            "XY34 ZZZ",
            28700,
            "Alex",
            "Morgan",
            "alex.morgan@example.com",
            "07700900456",
            None,
        ),
    )
    timestamp = iso_now()

    for (
        booking_id,
        reference,
        slot_id,
        registration,
        mileage,
        first_name,
        last_name,
        email,
        phone,
        notes,
    ) in bookings:
        slot = connection.execute(
            """
            SELECT dealership_id, service_type_id
            FROM workshop_slots
            WHERE id = ?
            """,
            (slot_id,),
        ).fetchone()
        if slot is None:
            raise RuntimeError(f"Seeded workshop slot {slot_id} was not found.")
        connection.execute(
            """
            INSERT INTO workshop_bookings
            (id, reference, slot_id, dealership_id, service_type_id, registration,
             mileage, first_name, last_name, email, phone, notes, status,
             created_at, updated_at, cancelled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, NULL)
            """,
            (
                booking_id,
                reference,
                slot_id,
                slot["dealership_id"],
                slot["service_type_id"],
                registration,
                mileage,
                first_name,
                last_name,
                email,
                phone,
                notes,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "UPDATE workshop_slots SET status = 'booked' WHERE id = ?",
            (slot_id,),
        )
