from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
API_KEY = "northstar-local-development"
ADMIN_KEY = "northstar-local-admin"
CONTACT = {
    "firstName": "Jamie",
    "lastName": "Taylor",
    "email": "jamie@example.com",
    "phone": "07700900123",
}


class HttpApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.port = cls._available_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        environment = {
            **os.environ,
            "NORTHSTAR_API_KEY": API_KEY,
            "NORTHSTAR_ADMIN_KEY": ADMIN_KEY,
            "NORTHSTAR_DATA_PATH": str(
                Path(cls.temporary_directory.name) / "http-platform.sqlite3"
            ),
            "NORTHSTAR_PORT": str(cls.port),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "src.server"],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        cls._wait_until_ready()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.server.poll() is None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                cls.server.kill()
                cls.server.wait(timeout=3)
        if cls.server.stdout:
            cls.server.stdout.close()
        cls.temporary_directory.cleanup()

    @staticmethod
    def _available_port() -> int:
        with socket.socket() as server_socket:
            server_socket.bind(("127.0.0.1", 0))
            return int(server_socket.getsockname()[1])

    @classmethod
    def _wait_until_ready(cls) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                output = cls.server.stdout.read() if cls.server.stdout else ""
                raise RuntimeError(f"HTTP test server stopped during startup:\n{output}")
            try:
                with urlopen(f"{cls.base_url}/health", timeout=1) as response:
                    if response.status == 200:
                        return
            except (HTTPError, URLError):
                time.sleep(0.05)
        raise RuntimeError("HTTP test server did not become ready.")

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_status: int = 200,
    ) -> dict[str, Any]:
        request_headers = {"Accept": "application/json", **(headers or {})}
        encoded_body = None
        if body is not None:
            encoded_body = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}",
            data=encoded_body,
            headers=request_headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=3) as response:
                status = response.status
                payload = json.loads(response.read())
        except HTTPError as error:
            try:
                status = error.code
                payload = json.loads(error.read())
            finally:
                error.close()
        self.assertEqual(
            status,
            expected_status,
            f"{method} {path} returned {status}: {payload}",
        )
        return payload

    def api(
        self,
        method: str,
        path: str,
        contract_path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_status: int = 200,
    ) -> dict[str, Any]:
        self.exercised_operations.add((method, contract_path))
        return self.request(
            method,
            path,
            body=body,
            headers=headers,
            expected_status=expected_status,
        )

    def test_every_documented_operation_over_http(self) -> None:
        self.exercised_operations: set[tuple[str, str]] = set()
        authenticated = {"X-API-Key": API_KEY}

        dealerships = self.api("GET", "/api/dealerships", "/api/dealerships")
        self.assertEqual(len(dealerships["items"]), 4)
        dealership = self.api(
            "GET",
            "/api/dealerships/northstar-manchester",
            "/api/dealerships/{dealershipId}",
        )
        self.assertEqual(dealership["town"], "Manchester")
        opening_hours = self.api(
            "GET",
            "/api/dealerships/northstar-manchester/opening-hours",
            "/api/dealerships/{dealershipId}/opening-hours",
        )
        self.assertTrue(opening_hours["weekly"])
        self.assertTrue(opening_hours["holidayExceptions"])

        vehicles = self.api(
            "GET",
            "/api/vehicles?bodyStyle=SUV&fuelType=Hybrid&pageSize=50",
            "/api/vehicles",
        )
        self.assertTrue(vehicles["items"])
        vehicle = self.api(
            "GET",
            "/api/vehicles/veh-001",
            "/api/vehicles/{vehicleId}",
        )
        self.assertEqual(vehicle["id"], "veh-001")
        availability = self.api(
            "GET",
            "/api/vehicles/veh-001/availability",
            "/api/vehicles/{vehicleId}/availability",
        )
        self.assertEqual(availability["availability"], "available")

        offers = self.api("GET", "/api/offers?make=BMW", "/api/offers")
        self.assertTrue(offers["items"])
        offer = self.api("GET", "/api/offers/offer-01", "/api/offers/{offerId}")
        self.assertEqual(offer["id"], "offer-01")

        services = self.api("GET", "/api/service-types", "/api/service-types")
        self.assertEqual(len(services["items"]), 8)
        test_drive_slots = self.api(
            "GET",
            "/api/test-drive-slots?vehicleId=veh-001",
            "/api/test-drive-slots",
        )
        self.assertTrue(test_drive_slots["items"])

        self.request(
            "POST",
            "/api/sales-enquiries",
            body={
                "dealershipId": "northstar-manchester",
                "enquiryType": "general",
                **CONTACT,
                "message": "Please tell me about your current stock.",
            },
            expected_status=401,
        )
        sales_enquiry = self.api(
            "POST",
            "/api/sales-enquiries",
            "/api/sales-enquiries",
            body={
                "dealershipId": "northstar-manchester",
                "vehicleId": "veh-001",
                "enquiryType": "availability",
                **CONTACT,
                "message": "Is this vehicle available to view on Saturday?",
            },
            headers={**authenticated, "Idempotency-Key": "http-sales-enquiry"},
            expected_status=201,
        )
        saved_sales_enquiry = self.api(
            "GET",
            f"/api/sales-enquiries/{sales_enquiry['id']}",
            "/api/sales-enquiries/{recordId}",
            headers=authenticated,
        )
        self.assertEqual(saved_sales_enquiry["status"], "received")

        test_drive_body = {
            "slotId": test_drive_slots["items"][0]["id"],
            **CONTACT,
            "notes": "Automatic transmission preferred.",
        }
        test_drive_booking = self.api(
            "POST",
            "/api/test-drive-bookings",
            "/api/test-drive-bookings",
            body=test_drive_body,
            headers={**authenticated, "Idempotency-Key": "http-test-drive"},
            expected_status=201,
        )
        replayed_test_drive = self.request(
            "POST",
            "/api/test-drive-bookings",
            body=test_drive_body,
            headers={**authenticated, "Idempotency-Key": "http-test-drive"},
            expected_status=201,
        )
        self.assertTrue(replayed_test_drive["idempotentReplay"])
        saved_test_drive = self.api(
            "GET",
            f"/api/test-drive-bookings/{test_drive_booking['id']}",
            "/api/test-drive-bookings/{recordId}",
            headers=authenticated,
        )
        self.assertEqual(saved_test_drive["status"], "confirmed")
        slot_conflict = self.request(
            "POST",
            "/api/test-drive-bookings",
            body=test_drive_body,
            headers={**authenticated, "Idempotency-Key": "http-test-drive-conflict"},
            expected_status=409,
        )
        self.assertEqual(slot_conflict["error"]["code"], "SLOT_UNAVAILABLE")

        vehicle_interest = self.api(
            "POST",
            "/api/vehicle-interests",
            "/api/vehicle-interests",
            body={
                "vehicleId": "veh-007",
                **CONTACT,
                "notes": "Please contact me if the vehicle becomes available.",
            },
            headers={**authenticated, "Idempotency-Key": "http-interest"},
            expected_status=201,
        )
        saved_interest = self.api(
            "GET",
            f"/api/vehicle-interests/{vehicle_interest['id']}",
            "/api/vehicle-interests/{recordId}",
            headers=authenticated,
        )
        self.assertEqual(saved_interest["status"], "registered")

        callback = self.api(
            "POST",
            "/api/callback-requests",
            "/api/callback-requests",
            body={
                "dealershipId": "northstar-manchester",
                "department": "sales",
                "vehicleId": "veh-001",
                **CONTACT,
                "preferredTime": "Weekday afternoon",
                "reason": "I would like to discuss finance options.",
            },
            headers={**authenticated, "Idempotency-Key": "http-callback"},
            expected_status=201,
        )
        saved_callback = self.api(
            "GET",
            f"/api/callback-requests/{callback['id']}",
            "/api/callback-requests/{recordId}",
            headers=authenticated,
        )
        self.assertEqual(saved_callback["status"], "requested")

        workshop_locations = self.api(
            "GET",
            "/api/workshop-locations",
            "/api/workshop-locations",
        )
        self.assertEqual(len(workshop_locations["items"]), 4)
        workshop_slots = self.api(
            "GET",
            "/api/workshop-availability?dealershipId=northstar-manchester",
            "/api/workshop-availability",
        )
        self.assertGreaterEqual(len(workshop_slots["items"]), 2)
        workshop_booking = self.api(
            "POST",
            "/api/workshop-bookings",
            "/api/workshop-bookings",
            body={
                "slotId": workshop_slots["items"][0]["id"],
                "registration": "AB12 CDE",
                "mileage": 42000,
                **CONTACT,
                "notes": "Please inspect the brakes.",
            },
            headers={**authenticated, "Idempotency-Key": "http-workshop"},
            expected_status=201,
        )
        saved_workshop_booking = self.api(
            "GET",
            f"/api/workshop-bookings/{workshop_booking['id']}",
            "/api/workshop-bookings/{recordId}",
            headers=authenticated,
        )
        self.assertEqual(saved_workshop_booking["status"], "confirmed")
        amended_workshop_booking = self.api(
            "PATCH",
            f"/api/workshop-bookings/{workshop_booking['id']}",
            "/api/workshop-bookings/{recordId}",
            body={
                "slotId": workshop_slots["items"][1]["id"],
                "mileage": 42500,
                "notes": "Customer will wait at the dealership.",
            },
            headers=authenticated,
        )
        self.assertEqual(amended_workshop_booking["mileage"], 42500)
        cancelled_workshop_booking = self.api(
            "DELETE",
            f"/api/workshop-bookings/{workshop_booking['id']}",
            "/api/workshop-bookings/{recordId}",
            headers=authenticated,
        )
        self.assertEqual(cancelled_workshop_booking["status"], "cancelled")

        found_workshop_booking = self.api(
            "POST",
            "/api/workshop-bookings/lookup",
            "/api/workshop-bookings/lookup",
            body={
                "reference": "WORK-10001",
                "lastName": "Taylor",
                "registration": "AB12 CDE",
                "phone": "07700900123",
            },
            headers=authenticated,
        )
        self.assertEqual(found_workshop_booking["id"], "wsb-seeded-001")
        failed_lookup = self.request(
            "POST",
            "/api/workshop-bookings/lookup",
            body={
                "reference": "WORK-10001",
                "lastName": "Incorrect",
                "registration": "AB12 CDE",
                "phone": "07700900123",
            },
            headers=authenticated,
            expected_status=404,
        )
        self.assertEqual(failed_lookup["error"]["code"], "BOOKING_NOT_FOUND")

        message = self.api(
            "POST",
            "/api/dealership-messages",
            "/api/dealership-messages",
            body={
                "dealershipId": "northstar-manchester",
                "department": "parts",
                "subject": "Roof bar availability",
                "message": "Please let me know whether roof bars are in stock.",
                "preferredContactMethod": "email",
                **CONTACT,
            },
            headers={**authenticated, "Idempotency-Key": "http-message"},
            expected_status=201,
        )
        saved_message = self.api(
            "GET",
            f"/api/dealership-messages/{message['id']}",
            "/api/dealership-messages/{recordId}",
            headers=authenticated,
        )
        self.assertEqual(saved_message["status"], "received")

        valuation = self.api(
            "POST",
            "/api/part-exchange-valuations",
            "/api/part-exchange-valuations",
            body={
                "dealershipId": "northstar-manchester",
                "registration": "AB12 CDE",
                "mileage": 54000,
                "condition": "good",
                **CONTACT,
            },
            headers={**authenticated, "Idempotency-Key": "http-valuation"},
            expected_status=201,
        )
        self.assertGreater(
            valuation["estimateHighPence"],
            valuation["estimateLowPence"],
        )
        saved_valuation = self.api(
            "GET",
            f"/api/part-exchange-valuations/{valuation['id']}",
            "/api/part-exchange-valuations/{recordId}",
            headers=authenticated,
        )
        self.assertEqual(saved_valuation["status"], "estimated")

        business_information = self.api(
            "GET",
            "/api/business-information",
            "/api/business-information",
        )
        self.assertIn("finance", business_information)
        self.assertIn("privacyContact", business_information)

        invalid_contact = self.request(
            "POST",
            "/api/dealership-messages",
            body={
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
            headers=authenticated,
            expected_status=422,
        )
        self.assertEqual(invalid_contact["error"]["code"], "VALIDATION_ERROR")

        contract = self.request("GET", "/openapi.json")
        documented_operations = {
            (method.upper(), path)
            for path, path_definition in contract["paths"].items()
            for method in path_definition
            if method in {"get", "post", "patch", "delete"}
        }
        self.assertEqual(documented_operations, self.exercised_operations)

        summary = self.request("GET", "/admin/api/summary")
        self.assertEqual(summary["counts"]["salesEnquiries"], 1)
        self.assertEqual(summary["counts"]["workshopBookings"], 3)
        self.request(
            "POST",
            "/admin/api/reset",
            expected_status=401,
        )
        reset = self.request(
            "POST",
            "/admin/api/reset",
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        self.assertEqual(reset["status"], "reset")
        missing_after_reset = self.request(
            "GET",
            f"/api/sales-enquiries/{sales_enquiry['id']}",
            headers=authenticated,
            expected_status=404,
        )
        self.assertEqual(missing_after_reset["error"]["code"], "NOT_FOUND")
        restored_seed = self.request(
            "POST",
            "/api/workshop-bookings/lookup",
            body={
                "reference": "WORK-10001",
                "lastName": "Taylor",
                "registration": "AB12 CDE",
                "phone": "07700900123",
            },
            headers=authenticated,
        )
        self.assertEqual(restored_seed["id"], "wsb-seeded-001")


if __name__ == "__main__":
    unittest.main()
