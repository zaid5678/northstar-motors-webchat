from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .application import DealershipPlatform
from .database import Database
from .errors import ApiError
from .validation import require_object

UTC = timezone.utc
ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
OPENAPI_PATH = ROOT / "openapi.json"
DATA_PATH = os.getenv("NORTHSTAR_DATA_PATH", str(ROOT / "data" / "northstar.sqlite3"))
API_KEY = os.getenv("NORTHSTAR_API_KEY", "northstar-local-development")
ADMIN_KEY = os.getenv("NORTHSTAR_ADMIN_KEY", "northstar-local-admin")
PORT = int(os.getenv("NORTHSTAR_PORT", "4010"))

database = Database(DATA_PATH)
platform = DealershipPlatform(database)


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Handler(BaseHTTPRequestHandler):
    server_version = "NorthstarDealershipPlatform/1.0"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-API-Key, X-Admin-Key, Idempotency-Key",
        )
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        body: dict[str, Any] | None = None
        status = 200
        try:
            path, query = self._request_parts()
            if re.fullmatch(
                r"/api/(sales-enquiries|test-drive-bookings|vehicle-interests|callback-requests|workshop-bookings|dealership-messages|part-exchange-valuations)/[^/]+",
                path,
            ):
                self._require_api_key()
            response = self._dispatch_get(path, query)
            if isinstance(response, StaticResponse):
                self._send_bytes(response.content, response.content_type, response.status)
                status = response.status
            else:
                self._send_json(response)
        except ApiError as error:
            status = error.status
            self._send_json(error.to_response(), error.status)
        except Exception as error:
            status = 500
            self._send_json(
                {
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "The dealership platform could not process the request.",
                        "fieldErrors": {},
                        "retryable": True,
                    }
                },
                500,
            )
            print(f"Unhandled GET error: {error}", flush=True)
        finally:
            if self.path.startswith("/api/"):
                database.log_request("GET", self.path, status, body, now())

    def do_POST(self) -> None:  # noqa: N802
        self._handle_write("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle_write("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle_write("DELETE")

    def _handle_write(self, method: str) -> None:
        body: dict[str, Any] | None = None
        status = 200
        try:
            path, _ = self._request_parts()
            if path == "/admin/api/reset":
                self._require_admin_key()
                database.reset()
                response = {"status": "reset", "resetAt": now()}
            else:
                self._require_api_key()
                body = self._read_json() if method != "DELETE" else {}
                response, status = self._dispatch_write(
                    method,
                    path,
                    body,
                    self.headers.get("Idempotency-Key"),
                )
            self._send_json(response, status)
        except ApiError as error:
            status = error.status
            self._send_json(error.to_response(), error.status)
        except json.JSONDecodeError:
            status = 400
            error = ApiError(400, "INVALID_JSON", "The request body is not valid JSON.")
            self._send_json(error.to_response(), error.status)
        except Exception as error:
            status = 500
            self._send_json(
                {
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "The dealership platform could not process the request.",
                        "fieldErrors": {},
                        "retryable": True,
                    }
                },
                500,
            )
            print(f"Unhandled {method} error: {error}", flush=True)
        finally:
            if self.path.startswith("/api/"):
                database.log_request(method, self.path, status, body, now())

    def _dispatch_get(self, path: str, query: dict[str, str]) -> dict[str, Any] | "StaticResponse":
        if path == "/health":
            return {"status": "ok", "service": "northstar-dealership-platform", "time": now()}
        if path in {"/", "/docs"}:
            return self._static("docs.html", "text/html; charset=utf-8")
        if path == "/docs.js":
            return self._static("docs.js", "text/javascript; charset=utf-8")
        if path == "/admin":
            return self._static("admin.html", "text/html; charset=utf-8")
        if path == "/admin.js":
            return self._static("admin.js", "text/javascript; charset=utf-8")
        if path == "/platform.css":
            return self._static("platform.css", "text/css; charset=utf-8")
        if path == "/openapi.json":
            return StaticResponse(OPENAPI_PATH.read_bytes(), "application/json; charset=utf-8")
        if match := re.fullmatch(r"/assets/vehicles/([a-z0-9-]+)\.jpg", path):
            image_path = PUBLIC / "assets" / "vehicles" / f"{match.group(1)}.jpg"
            if not image_path.is_file():
                raise ApiError(404, "NOT_FOUND", "Vehicle image was not found.")
            return StaticResponse(image_path.read_bytes(), "image/jpeg")
        if match := re.fullmatch(r"/assets/vehicles/([a-z0-9-]+)\.svg", path):
            return StaticResponse(
                self._vehicle_svg(match.group(1), query.get("view", "front")),
                "image/svg+xml; charset=utf-8",
            )
        if path == "/admin/api/summary":
            return platform.admin_summary()
        if path == "/api/dealerships":
            return platform.list_dealerships()
        if match := re.fullmatch(r"/api/dealerships/([^/]+)/opening-hours", path):
            return platform.get_opening_hours(match.group(1))
        if match := re.fullmatch(r"/api/dealerships/([^/]+)", path):
            return platform.get_dealership(match.group(1))
        if path == "/api/vehicles":
            return platform.list_vehicles(query)
        if match := re.fullmatch(r"/api/vehicles/([^/]+)/availability", path):
            return platform.get_vehicle_availability(match.group(1))
        if match := re.fullmatch(r"/api/vehicles/([^/]+)", path):
            return platform.get_vehicle(match.group(1))
        if path == "/api/offers":
            return platform.list_offers(query)
        if match := re.fullmatch(r"/api/offers/([^/]+)", path):
            return platform.get_offer(match.group(1))
        if path == "/api/service-types":
            return platform.list_service_types()
        if path == "/api/test-drive-slots":
            return platform.list_test_drive_slots(query)
        if path == "/api/workshop-locations":
            return platform.list_workshop_locations()
        if path == "/api/workshop-availability":
            return platform.list_workshop_availability(query)
        if path == "/api/business-information":
            return platform.get_business_information()
        if match := re.fullmatch(
            r"/api/(sales-enquiries|test-drive-bookings|vehicle-interests|callback-requests|workshop-bookings|dealership-messages|part-exchange-valuations)/([^/]+)",
            path,
        ):
            return platform.get_record(match.group(1), match.group(2))
        raise ApiError(404, "NOT_FOUND", "Endpoint was not found.")

    def _dispatch_write(
        self,
        method: str,
        path: str,
        body: dict[str, Any],
        idempotency_key: str | None,
    ) -> tuple[dict[str, Any], int]:
        body = require_object(body)
        if method == "POST" and path == "/api/sales-enquiries":
            return platform.create_sales_enquiry(body, idempotency_key), 201
        if method == "POST" and path == "/api/test-drive-bookings":
            return platform.create_test_drive_booking(body, idempotency_key), 201
        if method == "POST" and path == "/api/vehicle-interests":
            return platform.create_vehicle_interest(body, idempotency_key), 201
        if method == "POST" and path == "/api/callback-requests":
            return platform.create_callback_request(body, idempotency_key), 201
        if method == "POST" and path == "/api/workshop-bookings":
            return platform.create_workshop_booking(body, idempotency_key), 201
        if method == "POST" and path == "/api/workshop-bookings/lookup":
            return platform.find_workshop_booking(body), 200
        if method == "POST" and path == "/api/dealership-messages":
            return platform.create_dealership_message(body, idempotency_key), 201
        if method == "POST" and path == "/api/part-exchange-valuations":
            return platform.create_part_exchange_valuation(body, idempotency_key), 201
        if method == "PATCH" and (
            match := re.fullmatch(r"/api/workshop-bookings/([^/]+)", path)
        ):
            return platform.update_workshop_booking(match.group(1), body), 200
        if method == "DELETE" and (
            match := re.fullmatch(r"/api/workshop-bookings/([^/]+)", path)
        ):
            return platform.cancel_workshop_booking(match.group(1)), 200
        raise ApiError(404, "NOT_FOUND", "Endpoint was not found.")

    def _request_parts(self) -> tuple[str, dict[str, str]]:
        parsed = urlparse(self.path)
        return parsed.path.rstrip("/") or "/", {
            key: values[-1] for key, values in parse_qs(parsed.query).items()
        }

    def _read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > 100_000:
            raise ApiError(413, "REQUEST_TOO_LARGE", "Request body is too large.")
        if content_length == 0:
            return {}
        return json.loads(self.rfile.read(content_length).decode("utf-8"))

    def _require_api_key(self) -> None:
        if self.headers.get("X-API-Key") != API_KEY:
            raise ApiError(
                401,
                "UNAUTHORISED",
                "Send the local API key using the X-API-Key header.",
            )

    def _require_admin_key(self) -> None:
        if self.headers.get("X-Admin-Key") != ADMIN_KEY:
            raise ApiError(401, "UNAUTHORISED", "The local admin key is required.")

    def _static(self, name: str, content_type: str) -> "StaticResponse":
        path = PUBLIC / name
        if not path.is_file():
            raise ApiError(404, "NOT_FOUND", "Asset was not found.")
        return StaticResponse(path.read_bytes(), content_type)

    def _vehicle_svg(self, vehicle_id: str, view: str) -> bytes:
        try:
            vehicle = platform.get_vehicle(vehicle_id)
        except ApiError:
            raise ApiError(404, "NOT_FOUND", "Vehicle image was not found.") from None
        palette = {
            "Alpine White": ("#dfe4e7", "#9aa5ab"),
            "Midnight Black": ("#20272b", "#0b0f11"),
            "Atlantic Blue": ("#315c78", "#163447"),
            "Silver Grey": ("#929da2", "#566269"),
            "Forest Green": ("#3c6157", "#1d3932"),
        }
        primary, shadow = palette.get(vehicle["colour"], ("#405c6b", "#1d3038"))
        offset = {"front": 0, "side": 14, "detail": -10}.get(view, 0)
        label = escape(f"{vehicle['year']} {vehicle['make']} {vehicle['model']}")
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" viewBox="0 0 1200 760" role="img" aria-label="{label}">
<defs>
  <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#edf2f2"/><stop offset="1" stop-color="#cbd7d5"/></linearGradient>
  <linearGradient id="paint" x1="0" x2="1"><stop stop-color="{shadow}"/><stop offset=".45" stop-color="{primary}"/><stop offset="1" stop-color="{shadow}"/></linearGradient>
  <filter id="blur"><feGaussianBlur stdDeviation="16"/></filter>
</defs>
<rect width="1200" height="760" fill="url(#sky)"/>
<path d="M0 520 C260 480 870 490 1200 530 V760 H0Z" fill="#bbc6c4"/>
<ellipse cx="{600 + offset}" cy="598" rx="405" ry="42" fill="#596363" opacity=".35" filter="url(#blur)"/>
<g transform="translate({offset} 0)">
  <path d="M210 506 L270 414 Q300 377 365 363 L648 315 Q752 298 826 352 L927 425 Q983 443 1007 502 L987 572 H209Z" fill="url(#paint)"/>
  <path d="M394 371 L653 332 Q727 322 786 363 L847 414 H338Z" fill="#b8d1d7" opacity=".76"/>
  <path d="M627 338 L609 414 H829 L778 367 Q726 331 655 337Z" fill="#9bbac2" opacity=".82"/>
  <path d="M339 414 L390 374 L611 339 L592 414Z" fill="#abc7ce" opacity=".84"/>
  <path d="M250 492 Q531 447 959 479" stroke="#ffffff" stroke-opacity=".28" stroke-width="8" fill="none"/>
  <rect x="823" y="444" width="132" height="30" rx="14" fill="#f2e8b8"/>
  <rect x="224" y="461" width="78" height="30" rx="14" fill="#d44d44"/>
  <circle cx="365" cy="555" r="87" fill="#232a2d"/><circle cx="365" cy="555" r="49" fill="#9aa3a6"/><circle cx="365" cy="555" r="16" fill="#3d464a"/>
  <circle cx="848" cy="555" r="87" fill="#232a2d"/><circle cx="848" cy="555" r="49" fill="#9aa3a6"/><circle cx="848" cy="555" r="16" fill="#3d464a"/>
  <rect x="535" y="519" width="138" height="46" rx="5" fill="#f3f1e7"/>
  <text x="604" y="549" font-family="Arial, sans-serif" font-size="20" font-weight="700" text-anchor="middle" fill="#172329">NORTHSTAR</text>
</g>
<text x="64" y="92" font-family="Arial, sans-serif" font-size="20" letter-spacing="4" fill="#5b6e72">NORTHSTAR SELECTED</text>
<text x="64" y="142" font-family="Arial, sans-serif" font-size="34" font-weight="700" fill="#172329">{label}</text>
</svg>"""
        return svg.encode("utf-8")

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        content = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._send_bytes(content, "application/json; charset=utf-8", status)

    def _send_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)


class StaticResponse:
    def __init__(self, content: bytes, content_type: str, status: int = 200) -> None:
        self.content = content
        self.content_type = content_type
        self.status = status


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Northstar dealership platform listening on http://0.0.0.0:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
