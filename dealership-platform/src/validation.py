from __future__ import annotations

import re
from typing import Any

from .errors import ApiError

EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PHONE_PATTERN = re.compile(r"^(?:\+44|0)(?:1|7)\d{8,9}$")
REGISTRATION_PATTERN = re.compile(r"^[A-Z0-9 ]{2,10}$")


def normalize_phone(value: Any) -> str:
    phone = re.sub(r"[\s().-]", "", str(value or "").strip())
    return f"0{phone[3:]}" if phone.startswith("+44") else phone


def require_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ApiError(400, "INVALID_JSON", "The request body must be a JSON object.")
    return value


def require_string(
    body: dict[str, Any],
    field: str,
    *,
    minimum: int = 1,
    maximum: int = 500,
) -> str:
    value = body.get(field)
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Some request fields are invalid.",
            field_errors={field: "This field is required."},
        )
    value = value.strip()
    if len(value) > maximum:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Some request fields are invalid.",
            field_errors={field: f"Use no more than {maximum} characters."},
        )
    return value


def optional_string(body: dict[str, Any], field: str, *, maximum: int = 2_000) -> str | None:
    value = body.get(field)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Some request fields are invalid.",
            field_errors={field: "This field must be text."},
        )
    value = value.strip()
    if len(value) > maximum:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Some request fields are invalid.",
            field_errors={field: f"Use no more than {maximum} characters."},
        )
    return value or None


def require_integer(
    body: dict[str, Any],
    field: str,
    *,
    minimum: int = 0,
    maximum: int = 10_000_000,
) -> int:
    value = body.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Some request fields are invalid.",
            field_errors={field: f"Enter a whole number between {minimum} and {maximum}."},
        )
    return value


def require_enum(body: dict[str, Any], field: str, allowed: set[str]) -> str:
    value = body.get(field)
    if value not in allowed:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Some request fields are invalid.",
            field_errors={field: f"Choose one of: {', '.join(sorted(allowed))}."},
        )
    return str(value)


def validate_contact(body: dict[str, Any]) -> dict[str, str]:
    field_errors: dict[str, str] = {}

    first_name = str(body.get("firstName", "")).strip()
    last_name = str(body.get("lastName", "")).strip()
    email = str(body.get("email", "")).strip()
    phone = normalize_phone(body.get("phone"))

    if len(first_name) < 2 or any(char.isdigit() for char in first_name):
        field_errors["firstName"] = "Enter a valid first name."
    if len(last_name) < 2 or any(char.isdigit() for char in last_name):
        field_errors["lastName"] = "Enter a valid last name."
    if not EMAIL_PATTERN.match(email):
        field_errors["email"] = "Enter a complete email address."
    if not PHONE_PATTERN.match(phone):
        field_errors["phone"] = "Enter a UK 01 landline or 07 mobile number."

    if field_errors:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Some contact details are invalid.",
            field_errors=field_errors,
        )

    return {
        "firstName": first_name,
        "lastName": last_name,
        "email": email,
        "phone": phone,
    }


def validate_phone(value: Any) -> str:
    phone = normalize_phone(value)
    if not PHONE_PATTERN.match(phone):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Some contact details are invalid.",
            field_errors={"phone": "Enter a UK 01 landline or 07 mobile number."},
        )
    return phone


def validate_registration(value: Any) -> str:
    registration = str(value or "").strip().upper()
    if not REGISTRATION_PATTERN.match(registration):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Some vehicle details are invalid.",
            field_errors={"registration": "Enter a valid registration."},
        )
    return registration
