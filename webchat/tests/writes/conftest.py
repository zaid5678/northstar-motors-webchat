"""Failure injection against the real, unmodified platform.

`Faults` sits between the client and the platform. It can let a request land and then lose the
response, which is the case a fake platform cannot prove anything about.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.support import PLATFORM_COMMAND, START_LIMIT_SECONDS

from northstar_webchat.dealership import DealershipClient, RetryPolicy
from northstar_webchat.dealership import outcomes as o
from northstar_webchat.dealership.models import TestDriveSlot, WorkshopSlot
from northstar_webchat.persistence.store import Store
from northstar_webchat.workflows.types import WorkflowKind
from northstar_webchat.writes.service import View, WriteService

PLATFORM_DIR = Path(__file__).resolve().parents[3] / "dealership-platform"
API_KEY = "northstar-local-development"
ADMIN_KEY = "northstar-local-admin"
CONTACT = {
    "first_name": "Probe",
    "last_name": "Tester",
    "email": "probe@example.com",
    "phone": "07700900999",
}
TAYLOR = {
    "reference": "WORK-10001",
    "last_name": "Taylor",
    "registration": "AB12 CDE",
    "phone": "07700900123",
}
FAST_RETRY = RetryPolicy(max_attempts=3, base_delay=0.001, max_delay=0.002)


@dataclass
class Rule:
    method: str
    path: str
    action: str
    times: int
    error: Exception | None = None
    rewrite: Callable[[httpx.Response], httpx.Response] | None = None
    gate: asyncio.Event | None = None
    arrived: asyncio.Event | None = None


class Faults(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.rules: list[Rule] = []
        self.sent: list[str] = []
        self.pending: list[asyncio.Task[None]] = []

    def lose_response(
        self, method: str, path: str, *, times: int = 1, error: Exception | None = None
    ) -> None:
        """The platform processes the request, and the answer never arrives."""
        self.rules.append(
            Rule(method, path, "lose", times, error or httpx.ReadTimeout("response lost"))
        )

    def never_arrive(self, method: str, path: str, *, times: int = 1) -> None:
        self.rules.append(Rule(method, path, "block", times, httpx.ConnectError("refused")))

    def rewrite(
        self,
        method: str,
        path: str,
        rewrite: Callable[[httpx.Response], httpx.Response],
        *,
        times: int = 1,
    ) -> None:
        self.rules.append(Rule(method, path, "rewrite", times, rewrite=rewrite))

    def hold(self, method: str, path: str) -> tuple[asyncio.Event, asyncio.Event]:
        """Pauses after the platform answers. Returns (arrived, release)."""
        arrived, release = asyncio.Event(), asyncio.Event()
        self.rules.append(Rule(method, path, "hold", 1, gate=release, arrived=arrived))
        return arrived, release

    def arrive_late(self, method: str, path: str) -> asyncio.Event:
        """The client gives up, but the request lands later. Returns the event that lands it."""
        release = asyncio.Event()
        self.rules.append(
            Rule(method, path, "late", 1, httpx.ReadTimeout("gave up"), gate=release)
        )
        return release

    def _take(self, request: httpx.Request) -> Rule | None:
        for rule in self.rules:
            if rule.times > 0 and rule.method == request.method and rule.path in request.url.path:
                rule.times -= 1
                return rule
        return None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        rule = self._take(request)
        if rule and rule.action == "block":
            assert rule.error is not None
            raise rule.error
        self.sent.append(f"{request.method} {request.url.path}")
        if rule and rule.action == "late":
            assert rule.error is not None and rule.gate is not None
            self.pending.append(asyncio.create_task(self._land_later(request, rule.gate)))
            raise rule.error
        response = await self.inner.handle_async_request(request)
        content = await response.aread()
        await response.aclose()
        built = httpx.Response(response.status_code, content=content, request=request)
        if rule is None:
            return built
        if rule.action == "lose":
            assert rule.error is not None
            raise rule.error
        if rule.action == "rewrite":
            assert rule.rewrite is not None
            return rule.rewrite(built)
        assert rule.arrived is not None and rule.gate is not None
        rule.arrived.set()
        await rule.gate.wait()
        return built

    async def _land_later(self, request: httpx.Request, gate: asyncio.Event) -> None:
        await gate.wait()
        response = await self.inner.handle_async_request(request)
        await response.aread()
        await response.aclose()

    async def aclose(self) -> None:
        await self.inner.aclose()


def writes_sent(faults: Faults, path: str) -> int:
    """Lookups are POSTs that change nothing, so they do not count."""
    return sum(
        1
        for item in faults.sent
        if item.startswith(("POST", "PATCH", "DELETE")) and path in item and "/lookup" not in item
    )


@pytest.fixture(scope="session")
def platform(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        PLATFORM_COMMAND,
        cwd=PLATFORM_DIR,
        env={
            **os.environ,
            "NORTHSTAR_API_KEY": API_KEY,
            "NORTHSTAR_ADMIN_KEY": ADMIN_KEY,
            "NORTHSTAR_DATA_PATH": str(tmp_path_factory.mktemp("platform") / "live.sqlite3"),
            "NORTHSTAR_PORT": str(port),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + START_LIMIT_SECONDS
    while True:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=1):
                break
        except (urllib.error.URLError, ConnectionError):
            if time.monotonic() > deadline:
                process.terminate()
                raise RuntimeError("the platform did not start")
            time.sleep(0.05)
    yield base
    process.terminate()
    process.wait(timeout=5)


def counts(base: str) -> dict[str, int]:
    """What really exists on the platform. Tests use this as the source of truth."""
    with urllib.request.urlopen(f"{base}/admin/api/summary", timeout=5) as response:
        return json.loads(response.read())["counts"]  # type: ignore[no-any-return]


@pytest.fixture
def clean_platform(platform: str) -> str:
    request = urllib.request.Request(
        f"{platform}/admin/api/reset", method="POST", headers={"X-Admin-Key": ADMIN_KEY}
    )
    urllib.request.urlopen(request, timeout=5).close()
    return platform


@pytest.fixture
def faults() -> Faults:
    return Faults()


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path}/chat.sqlite3"


@pytest.fixture
def store(db_url: str) -> Store:
    instance = Store(db_url)
    instance.migrate()
    return instance


@pytest.fixture
async def client(clean_platform: str, faults: Faults) -> AsyncIterator[DealershipClient]:
    async with DealershipClient(
        clean_platform, API_KEY, retry=FAST_RETRY, transport=faults
    ) as instance:
        yield instance


def build_service(store: Store, client: DealershipClient) -> WriteService:
    return WriteService(store, client, pepper=b"test-pepper", reconcile_delay=0.0)


@pytest.fixture
def service(store: Store, client: DealershipClient) -> WriteService:
    return build_service(store, client)


async def first_drive_slot(client: DealershipClient, vehicle_id: str = "veh-001") -> TestDriveSlot:
    found = await client.find_test_drive_slots(vehicle_id)
    assert isinstance(found, o.TestDriveSlotsFound)
    return found.slots[0]


async def first_workshop_slot(client: DealershipClient) -> WorkshopSlot:
    found = await client.find_workshop_slots(
        dealership_id="northstar-manchester", service_type_id="mot"
    )
    assert isinstance(found, o.WorkshopSlotsFound)
    return found.slots[0]


async def ready_test_drive(
    service: WriteService, client: DealershipClient, session_id: str = "s1"
) -> View:
    """Fills every slot through the customer channel, up to the confirmation block."""
    slot = await first_drive_slot(client)
    await service.start(session_id, WorkflowKind.TEST_DRIVE)
    await service.fill(session_id, "vehicle_id", "veh-001")
    await service.fill(session_id, "slot_id", slot.id)
    view = None
    for name, value in CONTACT.items():
        view = await service.fill(session_id, name, value)
    assert view is not None
    assert view.status == "confirming", view
    return view


async def ready_callback(service: WriteService, session_id: str = "s1") -> View:
    values: dict[str, Any] = {
        "dealership_id": "northstar-manchester",
        "department": "sales",
        "reason": "Discuss finance options",
        **CONTACT,
    }
    await service.start(session_id, WorkflowKind.CALLBACK)
    view = None
    for name, value in values.items():
        view = await service.fill(session_id, name, value)
    assert view is not None
    assert view.status == "confirming", view
    return view


async def ready_cancel(service: WriteService, session_id: str = "s1") -> View:
    await service.start(session_id, WorkflowKind.WORKSHOP_CANCEL)
    view = await service.submit_identity(session_id, **TAYLOR)
    assert view.status == "confirming", view
    return view


def token_of(view: View) -> str:
    assert view.block is not None
    return view.block.token
