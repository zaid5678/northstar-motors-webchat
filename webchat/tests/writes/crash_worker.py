"""Runs a real write in its own process and dies with `os._exit`, so no cleanup can run.

Usage: crash_worker.py <platform> <database> <scenario> <when>
`when` is `before` (the request never leaves) or `after` (the platform has processed it).
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.persistence.store import Store
from northstar_webchat.writes.service import WriteService

from tests.writes.conftest import (
    API_KEY,
    Faults,
    ready_cancel,
    ready_test_drive,
    token_of,
)

CRASHED = 137


class CrashingTransport(Faults):
    def __init__(self, method: str, path: str, when: str) -> None:
        super().__init__()
        self.method, self.path, self.when = method, path, when

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        target = request.method == self.method and self.path in request.url.path
        if target and self.when == "before":
            os._exit(CRASHED)
        response = await super().handle_async_request(request)
        if target:
            os._exit(CRASHED)
        return response


async def main(platform: str, database: str, scenario: str, when: str) -> None:
    store = Store(database)
    method, path = (
        ("POST", "/api/test-drive-bookings")
        if scenario == "test_drive"
        else ("DELETE", "/api/workshop-bookings/")
    )
    transport = CrashingTransport(method, path, when)
    async with DealershipClient(platform, API_KEY, transport=transport) as client:
        service = WriteService(store, client, pepper=b"test-pepper", reconcile_delay=0.0)
        if scenario == "test_drive":
            view = await ready_test_drive(service, client)
        else:
            view = await ready_cancel(service)
        await service.confirm("s1", token_of(view))
    raise SystemExit("the worker was meant to crash")


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:5]))
