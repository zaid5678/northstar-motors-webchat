from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.application import DealershipPlatform
from src.database import Database
from src.errors import ApiError


CONTACT = {
    "firstName": "Jamie",
    "lastName": "Taylor",
    "email": "jamie@example.com",
    "phone": "07700900123",
}


class PlatformTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        path = Path(self.temporary_directory.name) / "platform.sqlite3"
        self.database = Database(str(path))
        self.platform = DealershipPlatform(self.database)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def first_test_drive_slot(self, vehicle_id: str = "veh-001") -> str:
        slots = self.platform.list_test_drive_slots({"vehicleId": vehicle_id})["items"]
        self.assertGreater(len(slots), 0)
        return slots[0]["id"]

    def first_workshop_slot(self, excluded_slot: str | None = None) -> str:
        slots = self.platform.list_workshop_availability({})["items"]
        matching = [slot for slot in slots if slot["id"] != excluded_slot]
        self.assertGreater(len(matching), 0)
        return matching[0]["id"]

    def test_seeded_inventory_and_locations_are_available(self) -> None:
        dealerships = self.platform.list_dealerships()
        vehicles = self.platform.list_vehicles({"pageSize": "50"})

        self.assertEqual(len(dealerships["items"]), 4)
        self.assertEqual(vehicles["pagination"]["totalItems"], 60)
        self.assertEqual(self.platform.get_vehicle("veh-007")["availability"], "reserved")
        self.assertIsNone(self.platform.get_vehicle("veh-019")["pricePence"])

    def test_sales_enquiry_is_saved_as_received(self) -> None:
        enquiry = self.platform.create_sales_enquiry(
            {
                "dealershipId": "northstar-manchester",
                "vehicleId": "veh-001",
                "enquiryType": "availability",
                **CONTACT,
                "message": "Is this available to view on Saturday?",
            },
            "sales-enquiry-one",
        )

        self.assertEqual(enquiry["status"], "received")
        self.assertTrue(enquiry["reference"].startswith("SALE-"))
        saved = self.platform.get_record("sales-enquiries", enquiry["id"])
        self.assertEqual(saved["email"], CONTACT["email"])

    def test_test_drive_booking_is_idempotent_and_claims_slot_once(self) -> None:
        body = {"slotId": self.first_test_drive_slot(), **CONTACT}

        first = self.platform.create_test_drive_booking(body, "test-drive-one")
        replay = self.platform.create_test_drive_booking(body, "test-drive-one")

        self.assertEqual(first["status"], "confirmed")
        self.assertEqual(first["id"], replay["id"])
        self.assertTrue(replay["idempotentReplay"])

        with self.assertRaises(ApiError) as raised:
            self.platform.create_test_drive_booking(body, "test-drive-two")
        self.assertEqual(raised.exception.code, "SLOT_UNAVAILABLE")

    def test_idempotency_key_cannot_be_reused_for_changed_request(self) -> None:
        body = {"slotId": self.first_test_drive_slot(), **CONTACT}
        self.platform.create_test_drive_booking(body, "shared-key")

        changed_body = {**body, "notes": "A changed request"}
        with self.assertRaises(ApiError) as raised:
            self.platform.create_test_drive_booking(changed_body, "shared-key")

        self.assertEqual(raised.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_interest_is_only_accepted_for_reserved_vehicle(self) -> None:
        reserved = self.platform.create_vehicle_interest(
            {"vehicleId": "veh-007", **CONTACT},
            "interest-reserved",
        )
        self.assertEqual(reserved["status"], "registered")

        with self.assertRaises(ApiError) as raised:
            self.platform.create_vehicle_interest(
                {"vehicleId": "veh-001", **CONTACT},
                "interest-available",
            )
        self.assertEqual(raised.exception.code, "VEHICLE_NOT_RESERVED")

    def test_workshop_booking_can_be_moved_and_cancelled(self) -> None:
        original_slot = self.first_workshop_slot()
        booking = self.platform.create_workshop_booking(
            {
                "slotId": original_slot,
                "registration": "AB12 CDE",
                "mileage": 42000,
                **CONTACT,
            },
            "workshop-one",
        )
        next_slot = self.first_workshop_slot(excluded_slot=original_slot)

        amended = self.platform.update_workshop_booking(
            booking["id"],
            {"slotId": next_slot, "mileage": 42500, "notes": "Please inspect the brakes."},
        )
        cancelled = self.platform.cancel_workshop_booking(booking["id"])

        self.assertEqual(amended["slotId"], next_slot)
        self.assertEqual(amended["mileage"], 42500)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNotNone(cancelled["cancelledAt"])

        available_ids = {
            slot["id"] for slot in self.platform.list_workshop_availability({})["items"]
        }
        self.assertIn(original_slot, available_ids)
        self.assertIn(next_slot, available_ids)

    def test_seeded_workshop_booking_can_be_found_after_identity_match(self) -> None:
        booking = self.platform.find_workshop_booking(
            {
                "reference": "work-10001",
                "lastName": "taylor",
                "registration": "ab12cde",
                "phone": "+44 7700 900123",
            }
        )

        self.assertEqual(booking["id"], "wsb-seeded-001")
        self.assertEqual(booking["status"], "confirmed")
        self.assertEqual(booking["serviceTypeName"], "Full service")
        self.assertEqual(booking["dealershipName"], "Northstar Manchester")
        self.assertEqual(booking["dealershipAddressLine"], "101 Kingsway")
        self.assertTrue(booking["startsAt"])

    def test_workshop_booking_lookup_rejects_non_matching_details(self) -> None:
        correct_details = {
            "reference": "WORK-10001",
            "lastName": "Taylor",
            "registration": "AB12 CDE",
            "phone": "07700900123",
        }
        incorrect_details = {
            "reference": "WORK-99999",
            "lastName": "Other",
            "registration": "ZZ99 ZZZ",
            "phone": "07700900999",
        }

        for field, incorrect_value in incorrect_details.items():
            with self.subTest(field=field):
                with self.assertRaises(ApiError) as raised:
                    self.platform.find_workshop_booking(
                        {**correct_details, field: incorrect_value}
                    )
                self.assertEqual(raised.exception.status, 404)
                self.assertEqual(raised.exception.code, "BOOKING_NOT_FOUND")

    def test_workshop_booking_lookup_validates_required_details(self) -> None:
        with self.assertRaises(ApiError) as raised:
            self.platform.find_workshop_booking(
                {
                    "reference": "WORK-10001",
                    "lastName": "Taylor",
                    "registration": "AB12 CDE",
                }
            )

        self.assertEqual(raised.exception.code, "VALIDATION_ERROR")
        self.assertEqual(
            raised.exception.field_errors,
            {"phone": "Enter a UK 01 landline or 07 mobile number."},
        )

    def test_contact_validation_returns_field_errors(self) -> None:
        with self.assertRaises(ApiError) as raised:
            self.platform.create_dealership_message(
                {
                    "dealershipId": "northstar-manchester",
                    "department": "general",
                    "subject": "Contact",
                    "message": "Please get in touch.",
                    "preferredContactMethod": "email",
                    "firstName": "J",
                    "lastName": "Taylor",
                    "email": "not-an-email",
                    "phone": "123",
                },
                None,
            )

        self.assertEqual(raised.exception.code, "VALIDATION_ERROR")
        self.assertEqual(
            set(raised.exception.field_errors),
            {"firstName", "email", "phone"},
        )

    def test_reset_restores_seed_data_and_removes_business_records(self) -> None:
        self.platform.create_sales_enquiry(
            {
                "dealershipId": "northstar-manchester",
                "enquiryType": "general",
                **CONTACT,
                "message": "Please tell me about your current stock.",
            },
            "before-reset",
        )

        self.database.reset()

        self.assertEqual(
            self.platform.list_vehicles({"pageSize": "1"})["pagination"]["totalItems"],
            60,
        )
        self.assertEqual(self.platform.admin_summary()["counts"]["salesEnquiries"], 0)
        self.assertEqual(self.platform.admin_summary()["counts"]["workshopBookings"], 2)


if __name__ == "__main__":
    unittest.main()
