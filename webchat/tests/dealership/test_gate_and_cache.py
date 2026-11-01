"""The concurrency gate and read cache (D-024). The platform refuses bursts, so we queue them."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from northstar_webchat.dealership import DealershipClient, RetryPolicy
from northstar_webchat.dealership import outcomes as o
from northstar_webchat.dealership.models import VehicleSearch

from .conftest import availability, dealership, page, td_slot, vehicle

BASE = "http://platform.test"


class Clock:
    def __init__(self) -> None:
        self.moment = 0.0

    def __call__(self) -> float:
        return self.moment


class Recorder(httpx.AsyncBaseTransport):
    """Answers like the platform, and records how many requests were in flight at once."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.active = 0
        self.peak = 0
        self.paths: list[str] = []
        self.fail_next = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.paths.append(request.url.path)
        await asyncio.sleep(self.delay)
        self.active -= 1
        if self.fail_next > 0:
            self.fail_next -= 1
            return httpx.Response(503, request=request)
        path = request.url.path
        payload: Any
        if path == "/api/dealerships":
            payload = {"items": [dealership()]}
        elif path.endswith("/availability"):
            payload = availability()
        elif path == "/api/test-drive-slots":
            payload = {"items": [td_slot()]}
        elif path == "/api/vehicles":
            payload = page([vehicle()])
        elif path.endswith("/opening-hours"):
            payload = {"dealershipId": "x", "weekly": [], "holidayExceptions": []}
        else:
            payload = {"items": []}
        return httpx.Response(200, content=json.dumps(payload), request=request)


def build(transport: Recorder, clock: Clock | None = None, **options: Any) -> DealershipClient:
    extra = {"monotonic": clock} if clock else {}
    return DealershipClient(BASE, "key", transport=transport, **extra, **options)


class TestGate:
    async def test_a_burst_never_exceeds_the_limit(self) -> None:
        transport = Recorder(delay=0.005)
        async with build(transport, max_concurrent=4) as client:
            results = await asyncio.gather(
                *(client.search_vehicles(VehicleSearch(q=f"q{n}")) for n in range(40))
            )
        assert all(isinstance(item, o.VehiclesListed) for item in results)
        assert transport.peak == 4

    async def test_the_default_limit_is_ten(self) -> None:
        transport = Recorder(delay=0.005)
        async with build(transport) as client:
            await asyncio.gather(
                *(client.search_vehicles(VehicleSearch(q=f"q{n}")) for n in range(40))
            )
        assert transport.peak == 10

    async def test_waiting_for_a_retry_does_not_hold_a_place(self) -> None:
        """Backoff must happen outside the gate, or failures would slow everyone else."""
        transport = Recorder()
        transport.fail_next = 1
        released = asyncio.Event()
        started = asyncio.Event()

        async def sleep(_: float) -> None:
            started.set()
            await released.wait()

        async with build(
            transport,
            max_concurrent=1,
            sleep=sleep,
            retry=RetryPolicy(max_attempts=2),
        ) as client:
            slow = asyncio.create_task(client.search_vehicles(VehicleSearch(q="slow")))
            await started.wait()
            other = await asyncio.wait_for(
                client.search_vehicles(VehicleSearch(q="other")), timeout=1
            )
            assert isinstance(other, o.VehiclesListed)
            released.set()
            assert isinstance(await slow, o.VehiclesListed)


class TestCache:
    async def test_a_repeat_catalogue_read_is_served_from_cache(self) -> None:
        transport = Recorder()
        async with build(transport) as client:
            first = await client.list_dealerships()
            second = await client.list_dealerships()
        assert first == second
        assert transport.paths.count("/api/dealerships") == 1

    async def test_simultaneous_misses_make_one_request(self) -> None:
        transport = Recorder(delay=0.005)
        async with build(transport) as client:
            await asyncio.gather(*(client.list_dealerships() for _ in range(20)))
        assert transport.paths.count("/api/dealerships") == 1

    async def test_vehicle_searches_expire_after_30_seconds(self) -> None:
        transport, clock = Recorder(), Clock()
        async with build(transport, clock) as client:
            await client.search_vehicles(VehicleSearch())
            clock.moment = 29
            await client.search_vehicles(VehicleSearch())
            assert len(transport.paths) == 1
            clock.moment = 31
            await client.search_vehicles(VehicleSearch())
        assert len(transport.paths) == 2

    async def test_reference_data_lasts_five_minutes(self) -> None:
        transport, clock = Recorder(), Clock()
        async with build(transport, clock) as client:
            await client.list_dealerships()
            clock.moment = 299
            await client.list_dealerships()
            assert len(transport.paths) == 1
            clock.moment = 301
            await client.list_dealerships()
        assert len(transport.paths) == 2

    async def test_different_searches_are_cached_separately(self) -> None:
        transport = Recorder()
        async with build(transport) as client:
            await client.search_vehicles(VehicleSearch(make="BMW"))
            await client.search_vehicles(VehicleSearch(make="Kia"))
        assert len(transport.paths) == 2

    async def test_a_failure_is_never_cached(self) -> None:
        transport = Recorder()
        transport.fail_next = 3
        async with build(transport, sleep=_no_wait) as client:
            assert isinstance(await client.list_dealerships(), o.PlatformUnavailable)
            assert isinstance(await client.list_dealerships(), o.DealershipsListed)

    async def test_the_cache_is_bounded(self) -> None:
        transport = Recorder()
        async with build(transport, cache_size=3) as client:
            for n in range(5):
                await client.search_vehicles(VehicleSearch(q=f"q{n}"))
            await client.search_vehicles(VehicleSearch(q="q0"))
        assert len(transport.paths) == 6

    async def test_availability_is_never_cached(self) -> None:
        transport = Recorder()
        async with build(transport) as client:
            await client.get_vehicle_status("veh-001")
            await client.get_vehicle_status("veh-001")
        assert len(transport.paths) == 2

    async def test_slots_are_never_cached(self) -> None:
        transport = Recorder()
        async with build(transport) as client:
            await client.find_test_drive_slots("veh-001")
            await client.find_test_drive_slots("veh-001")
            await client.find_workshop_slots()
            await client.find_workshop_slots()
        assert transport.paths.count("/api/test-drive-slots") == 2
        assert transport.paths.count("/api/workshop-availability") == 2

    async def test_a_fresh_read_skips_the_cache_and_refreshes_it(self) -> None:
        transport = Recorder()
        async with build(transport) as client:
            await client.get_opening_hours("x")
            await client.get_opening_hours("x", fresh=True)
            await client.get_opening_hours("x")
        assert len(transport.paths) == 2


async def _no_wait(_: float) -> None:
    return None
