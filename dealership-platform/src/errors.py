from __future__ import annotations


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        field_errors: dict[str, str] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field_errors = field_errors or {}
        self.retryable = retryable

    def to_response(self) -> dict[str, object]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "fieldErrors": self.field_errors,
                "retryable": self.retryable,
            }
        }

