"""Dealership contact details and workshop locations, written by code from the platform."""

from __future__ import annotations

import json

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.dealership import outcomes as o
from northstar_webchat.llm.scripted import ScriptedLLM
from northstar_webchat.turns.engine import TurnEngine
from northstar_webchat.writes.service import WriteService

from ..writes.conftest import (  # noqa: F401  (fixtures)
    clean_platform,
    client,
    db_url,
    faults,
    platform,
    service,
    store,
)

SITE = "http://localhost:4173"


def engine(llm: ScriptedLLM, client: DealershipClient, service: WriteService) -> TurnEngine:  # noqa: F811
    return TurnEngine(llm, client, service, site_url=SITE)


async def test_contact_details_include_the_email_address(
    client: DealershipClient, service: WriteService  # noqa: F811
) -> None:
    reply = await engine(ScriptedLLM([]), client, service).action("s1", "opening_hours")
    sites = await client.list_dealerships()
    assert isinstance(sites, o.DealershipsListed)
    for site in sites.dealerships:
        assert site.email in reply.text
        assert site.phone in reply.text


async def test_the_client_reads_workshop_locations(client: DealershipClient) -> None:  # noqa: F811
    found = await client.list_workshop_locations()
    assert isinstance(found, o.WorkshopLocationsListed)
    assert {site.town for site in found.locations} >= {"Bolton", "Manchester"}


async def test_service_types_name_the_workshop_locations(
    client: DealershipClient, service: WriteService  # noqa: F811
) -> None:
    llm = ScriptedLLM([json.dumps({"intent": "service_types"}), "We offer servicing."])
    reply = await engine(llm, client, service).message("s1", "what servicing do you do?")
    found = await client.list_workshop_locations()
    assert isinstance(found, o.WorkshopLocationsListed)
    for site in found.locations:
        assert f"{site.name}, {site.address_line}, {site.town} {site.postcode}" in reply.text
